#!/usr/bin/env python3
"""
Atlas - Supabase Key Generator

generate_supabase_keys.sh is a thin wrapper that runs this script.
Cross-platform utility for generating Supabase JWT secrets and tokens.
"""

import sys
from pathlib import Path
import click

# Add the current directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from utils.supabase_keys import SupabaseKeyGenerator
from utils.system import detect_os


class SupabaseKeyGeneratorCLI:
    """CLI interface for Supabase key generation."""
    
    def __init__(self):
        self.generator = SupabaseKeyGenerator()
        self.os_type = detect_os()
        
    def show_platform_warning(self):
        """Show platform-specific warnings if needed."""
        if self.os_type == "windows":
            print()
            print("⚠️  Windows detected: If you encounter any issues, please run this script in Git Bash or WSL.")
        
    def generate_keys(self):
        """Generate and update Supabase keys."""
        try:
            # Generate and update keys
            if self.generator.generate_and_update_env():
                print("✅ Supabase keys generated and updated successfully!")
                self.show_platform_warning()
                return True
            else:
                print("❌ Failed to update .env file with generated keys")
                return False
                
        except Exception as e:
            print(f"❌ Error generating keys: {e}")
            return False


@click.command()
@click.option('--help-usage', is_flag=True, help='Show detailed usage information')
def main(help_usage):
    """Generate Supabase JWT secrets and tokens for local development."""
    
    cli = SupabaseKeyGeneratorCLI()
    
    if help_usage:
        usage_text = """
Usage: python generate_supabase_keys.py [options]

This script generates secure Supabase JWT secrets and tokens for local development:
- SUPABASE_JWT_SECRET: Random 64-character hex secret for signing JWTs
- SUPABASE_ANON_KEY: JWT token for anonymous access (10-year expiry)
- SUPABASE_SERVICE_KEY: JWT token for service role access (10-year expiry)

The keys are automatically added/updated in your .env file.

Options:
  --help-usage   Show this detailed usage information
  --help         Show basic help message

Examples:
  python generate_supabase_keys.py          # Generate keys and update .env
  
Cross-platform compatibility:
- Works identically on Windows, macOS, and Linux
- Crypto uses only the Python standard library (CLI surface uses click)
- generate_supabase_keys.sh is a thin wrapper that runs this script
"""
        print(usage_text)
        return
    
    print("🔐 Atlas - Supabase Key Generator")
    print("=" * 50)
    
    try:
        if cli.generate_keys():
            sys.exit(0)
        else:
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n❌ Key generation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()