"""
Hosts file management utilities.

Python implementation of hosts-utils.sh functions.
"""

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional
from utils.system import detect_os, is_elevated, get_hosts_file_path


class HostsManager:
    """Manages hosts file entries for Atlas services."""

    @classmethod
    def _atlas_hosts_from_topology(cls) -> List[str]:
        """Derived once per process from Topology.aliases. Returns a fresh
        copy on each call.

        The list reflects whatever order Topology emits (currently
        canonical-order driven by ``build_topology``'s row traversal).
        The agreement test compares sets, not order — do not rely on
        ordering here. Cached by the canonical
        ``services.topology.get_topology`` LRU, so no per-class state is
        required.
        """
        from services.topology import get_topology
        return list(get_topology().aliases)

    @classmethod
    def _atomic_write_hosts(cls, hosts_file_path: str, content: str) -> None:
        """Write the hosts file atomically.

        Writes a sibling temp in the same directory, matches the original's
        mode + ownership, then ``os.replace``s into place — so a crash
        mid-write leaves the original /etc/hosts intact (not truncated to
        zero), avoiding a system-wide name-resolution outage. Mirrors the
        atomic writers used for .env/secrets (SourceOverrideManager /
        KeyGenerator / SupabaseKeyGenerator) but additionally preserves
        ownership, since /etc/hosts is root-owned when --setup-hosts runs
        under sudo.
        """
        dst = Path(hosts_file_path)
        st = dst.stat()
        fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp, st.st_mode)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except (OSError, PermissionError):
                # chown needs root; if unavailable the replace still lands —
                # only the owner field may differ, benign under the sudo path
                # that owns the write.
                pass
            os.replace(tmp, hosts_file_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def __init__(self):
        self.hosts_file_path = get_hosts_file_path()
        self.os_type = detect_os()
        # Optional logger callback (msg, level). When None, falls back to
        # plain print(). The Live region wires this so output flows through
        # the log pane instead of tearing the alternate screen.
        self._logger: Optional[Callable[[str, str], None]] = None

    def set_logger(self, logger: Optional[Callable[[str, str], None]]) -> None:
        """Inject a (msg, level) callback used by every status message
        emitted from this class. Pass None to revert to print()."""
        self._logger = logger

    def _log(self, message: str, level: str = "info") -> None:
        """Route a status message through the registered logger or print."""
        if self._logger is not None:
            try:
                self._logger(message, level)
                return
            except Exception:
                pass
        print(message)
        
    def get_atlas_hosts(self) -> List[str]:
        """
        Get the list of Atlas hostnames.
        Replicates the get_atlas_hosts() function from hosts-utils.sh.
        
        Returns:
            list: List of Atlas hostnames
        """
        return self._atlas_hosts_from_topology()
    
    def check_missing_hosts(self) -> List[str]:
        """
        Check which hosts entries are missing from the hosts file.
        Replicates the check_missing_hosts() function from hosts-utils.sh.
        
        Returns:
            list: List of missing hostnames
        """
        if not self.hosts_file_path or not Path(self.hosts_file_path).exists():
            return self.get_atlas_hosts()  # All are missing if no hosts file
            
        missing = []
        
        try:
            with open(self.hosts_file_path, 'r', encoding="utf-8") as f:
                hosts_content = f.read()

            # Whole-token comparison, mirroring remove_hosts_entries_silent:
            # the old \b-anchored regex false-positived on hyphenated user
            # entries (`my-n8n.localhost` contains the token-boundary match
            # for `n8n.localhost`), so --setup-hosts skipped adding the
            # real alias.
            present: set = set()
            for line in hosts_content.splitlines():
                # Strip trailing comments and require 127.0.0.1 as the
                # ADDRESS (first token): a commented-out
                # `# 127.0.0.1 n8n.localhost` line must not count as
                # present (it doesn't resolve).
                tokens = line.split("#", 1)[0].split()
                if tokens[:1] == ["127.0.0.1"]:
                    present.update(tokens[1:])
            for host in self.get_atlas_hosts():
                if host not in present:
                    missing.append(host)

        except (OSError, UnicodeDecodeError):
            # Conservative degrade: treat unreadable hosts file as
            # "all entries missing". Narrow to filesystem/encoding
            # errors so a future bug (e.g. malformed regex pattern)
            # surfaces loudly instead of being silently absorbed.
            return self.get_atlas_hosts()
            
        return missing
    
    def remove_hosts_entries_silent(self, hosts_file_path: str) -> bool:
        """
        Remove Atlas hosts entries without creating backup.
        Replicates the remove_hosts_entries_silent() function from hosts-utils.sh.
        
        Args:
            hosts_file_path: Path to hosts file
            
        Returns:
            bool: True if successful
        """
        try:
            with open(hosts_file_path, 'r', encoding="utf-8") as f:
                lines = f.readlines()
            
            # Filter out Atlas-related lines
            filtered_lines = []
            for line in lines:
                # Skip the comment line
                if "# Atlas subdomains" in line:
                    continue
                    
                # Skip any line with Atlas hostnames. Whole-token
                # comparison — a substring check deleted the user's own
                # entries like `127.0.0.1 my-n8n.localhost` because they
                # CONTAIN a stack alias.
                should_skip = False
                # Comment-stripped, address-anchored tokens — mirrors the
                # presence check; a commented-out user line mentioning a
                # stack alias must not be deleted.
                effective = line.split("#", 1)[0].split()
                line_tokens = effective[1:] if effective[:1] == ["127.0.0.1"] else []
                for host in self.get_atlas_hosts():
                    if host in line_tokens:
                        should_skip = True
                        break
                        
                if not should_skip:
                    filtered_lines.append(line)
            
            # Write back atomically: a truncate-write here would empty
            # /etc/hosts on a crash mid-write (system-wide name-resolution
            # outage). Mirrors the .env/keys atomic writers.
            self._atomic_write_hosts(hosts_file_path, "".join(filtered_lines))

            return True

        except (OSError, UnicodeDecodeError):
            return False

    def add_hosts_entries(self, hosts_file_path: str, create_backup: bool = True) -> bool:
        """
        Add Atlas hosts entries to the hosts file.
        Replicates the add_hosts_entries() function from hosts-utils.sh.
        
        Args:
            hosts_file_path: Path to hosts file
            create_backup: Whether to create a backup first
            
        Returns:
            bool: True if successful
        """
        try:
            if create_backup:
                backup_path = self._create_hosts_backup(hosts_file_path)
                if backup_path:
                    self._log(f"  • Created backup: {backup_path}", "info")
                    
            # Remove existing Atlas entries first (cleanup)
            self.remove_hosts_entries_silent(hosts_file_path)
            
            # Add new entries. Normalize the trailing boundary instead of
            # blindly appending "\n" — repeated --setup-hosts cycles
            # otherwise accumulate blank lines at EOF (remove never
            # strips them).
            try:
                with open(hosts_file_path, encoding="utf-8") as rf:
                    existing = rf.read()
            except OSError:
                # Read failed but a write might still succeed — bailing
                # beats truncating /etc/hosts down to just our block.
                return False
            normalized = existing.rstrip("\n")
            parts = []
            if normalized:
                parts.append(normalized + "\n\n")
            parts.append("# Atlas subdomains (added by start.py)\n")
            parts.extend(f"127.0.0.1 {host}\n" for host in self.get_atlas_hosts())
            # Atomic write — a truncate-write would empty /etc/hosts on a
            # crash mid-write (see _atomic_write_hosts).
            self._atomic_write_hosts(hosts_file_path, "".join(parts))

            return True

        except (OSError, UnicodeDecodeError):
            return False

    def remove_hosts_entries(self, hosts_file_path: str) -> bool:
        """
        Remove Atlas hosts entries with backup.
        Replicates the remove_hosts_entries() function from hosts-utils.sh.
        
        Args:
            hosts_file_path: Path to hosts file
            
        Returns:
            bool: True if successful
        """
        try:
            backup_path = self._create_hosts_backup(hosts_file_path)
            if backup_path:
                self._log(f"  • Created backup: {backup_path}", "info")

            return self.remove_hosts_entries_silent(hosts_file_path)

        except OSError:
            return False

    def _create_hosts_backup(self, hosts_file_path: str) -> Optional[str]:
        """
        Create a backup of the hosts file with timestamp.
        
        Args:
            hosts_file_path: Path to hosts file
            
        Returns:
            str: Path to backup file, or None if failed
        """
        try:
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            backup_path = f"{hosts_file_path}.backup.{timestamp}"
            shutil.copy2(hosts_file_path, backup_path)
            return backup_path
        except OSError:
            return None
    
    def setup_hosts_entries(self) -> bool:
        """
        Main function to setup Atlas hosts entries.
        Replicates the setup_hosts_entries() function from hosts-utils.sh.
        
        Returns:
            bool: True if successful
        """
        if not self.hosts_file_path:
            self._log(f"  • ❌ Cannot determine hosts file location for OS: {self.os_type}", "error")
            return False

        if not Path(self.hosts_file_path).exists():
            self._log(f"  • ❌ Hosts file not found: {self.hosts_file_path}", "error")
            return False

        # Check if we have elevated privileges
        if not is_elevated():
            self._log("  • ❌ Administrative privileges required to modify hosts file", "error")
            if self.os_type == "windows":
                self._log("    Please run as Administrator", "error")
            else:
                self._log("    Please run with sudo (Linux/macOS) or as Administrator (Windows)", "error")
            return False

        self._log("  • Setting up Atlas hosts entries...", "info")
        self._log(f"  • Hosts file: {self.hosts_file_path}", "info")

        # Check which entries are missing first
        missing = self.check_missing_hosts()

        if not missing:
            self._log("  • ✅ All Atlas hosts entries already exist", "success")
            self._log("  • Available aliases:", "info")
            for host in self.get_atlas_hosts():
                self._log(f"    - http://{host}", "info")
            return True

        self._log(f"  • Adding missing entries: {', '.join(missing)}", "info")

        # Add the missing entries
        if self.add_hosts_entries(self.hosts_file_path):
            self._log("  • ✅ Hosts entries added successfully", "success")
            self._log("  • You can now access services via:", "info")
            for host in self.get_atlas_hosts():
                self._log(f"    - http://{host}", "info")
            return True
        else:
            self._log("  • ❌ Failed to add hosts entries", "error")
            return False
    
    def cleanup_hosts_entries(self) -> bool:
        """
        Clean up Atlas hosts entries from the hosts file.
        Used by stop.py with --clean-hosts flag.
        
        Returns:
            bool: True if successful
        """
        if not self.hosts_file_path:
            self._log(f"  • ⚠️  Cannot determine hosts file location for OS: {self.os_type}", "warning")
            return False

        if not Path(self.hosts_file_path).exists():
            self._log(f"  • ⚠️  Hosts file not found: {self.hosts_file_path}", "warning")
            return False

        # Check if we have elevated privileges
        if not is_elevated():
            self._log("  • ❌ Administrative privileges required to modify hosts file", "error")
            if self.os_type == "windows":
                self._log("    Please run as Administrator", "error")
            else:
                self._log("    Please run with sudo (Linux/macOS) or as Administrator (Windows)", "error")

            self._log("    Or manually remove these entries from hosts file:", "info")
            for host in self.get_atlas_hosts():
                self._log(f"    127.0.0.1 {host}", "info")
            return False

        self._log("  • Removing Atlas hosts file entries...", "info")

        if self.remove_hosts_entries(self.hosts_file_path):
            self._log("  • ✅ Hosts entries removed successfully", "success")
            self._log("    The following entries were removed:", "info")
            for host in self.get_atlas_hosts():
                self._log(f"    127.0.0.1 {host}", "info")
            return True
        else:
            self._log("  • ❌ Failed to remove hosts entries", "error")
            return False

    def check_hosts_status(self) -> None:
        """
        Check and display the status of Atlas hosts entries.
        Used by start.py to show hosts status.
        """
        if not self.hosts_file_path or not Path(self.hosts_file_path).exists():
            self._log("  • ⚠️  Cannot access hosts file", "warning")
            self._log("    Manual setup may be required for subdomain access", "warning")
            return

        missing = self.check_missing_hosts()

        if missing:
            self._log(f"  • Missing hosts entries: {', '.join(missing)}", "warning")
            self._log("    Run with --setup-hosts to add them automatically", "info")
        else:
            self._log("  • ✅ All required hosts entries are present", "success")