"""
Supabase JWT key generation utilities.

Python implementation of generate_supabase_keys.sh functionality.
Generates JWT secrets and tokens for Supabase authentication.
"""

import json
import base64
import hmac
import hashlib
import secrets
import time
from pathlib import Path
from typing import Optional
from core.config_parser import ConfigParser
from utils.atomic_write import atomic_write_text


class SupabaseKeyGenerator:
    """Generates Supabase JWT secrets and tokens."""
    
    def __init__(self, root_dir: Optional[str] = None):
        """
        Initialize the key generator.
        
        Args:
            root_dir: Root directory containing .env file
        """
        if root_dir is None:
            # Default to parent directory of bootstrapper
            self.root_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.root_dir = Path(root_dir)
            
        self.config_parser = ConfigParser(str(self.root_dir))
        self.issuer = "supabase-local"
        self.expiry_days = 365 * 10  # 10 years
        
    def generate_jwt_secret(self) -> str:
        """
        Generate a secure JWT secret (64 hex characters).
        
        Returns:
            str: 64-character hexadecimal JWT secret
        """
        # Generate 32 random bytes and convert to hex (64 characters)
        return secrets.token_hex(32)
    
    def base64url_encode(self, data: str) -> str:
        """
        Encode data using base64url encoding (no padding, URL-safe).
        
        Args:
            data: String to encode
            
        Returns:
            str: Base64url encoded string
        """
        # Convert string to bytes, then base64 encode
        encoded = base64.b64encode(data.encode('utf-8'))
        # Convert to string and make URL-safe (replace + with -, / with _)
        # Remove padding (= characters)
        return encoded.decode('utf-8').replace('+', '-').replace('/', '_').rstrip('=')
    
    def create_jwt_token(self, secret: str, role: str) -> str:
        """
        Create a JWT token for the specified role.
        
        Args:
            secret: JWT secret for signing
            role: Role for the token ('anon' or 'service_role')
            
        Returns:
            str: JWT token
        """
        # Create JWT header
        header = {
            "alg": "HS256",
            "typ": "JWT"
        }
        
        # Create JWT payload with expiry
        current_time = int(time.time())
        expiry_time = current_time + (self.expiry_days * 24 * 60 * 60)
        
        payload = {
            "role": role,
            "iss": self.issuer,
            "exp": expiry_time
        }
        
        # Encode header and payload
        header_encoded = self.base64url_encode(json.dumps(header, separators=(',', ':')))
        payload_encoded = self.base64url_encode(json.dumps(payload, separators=(',', ':')))
        
        # Create the unsigned token
        unsigned_token = f"{header_encoded}.{payload_encoded}"
        
        # Create signature using HMAC-SHA256
        signature = hmac.new(
            secret.encode('utf-8'),
            unsigned_token.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        # Base64url encode the signature
        signature_encoded = base64.b64encode(signature).decode('utf-8')
        signature_encoded = signature_encoded.replace('+', '-').replace('/', '_').rstrip('=')
        
        # Return the complete JWT
        return f"{unsigned_token}.{signature_encoded}"
    
    def update_env_file(self, jwt_secret: str, anon_key: str, service_key: str) -> bool:
        """
        Update the .env file with the generated keys.
        
        Args:
            jwt_secret: JWT secret
            anon_key: Anonymous key
            service_key: Service role key
            
        Returns:
            bool: True if successful
        """
        env_file_path = self.config_parser.env_file_path
        
        # Create .env file if it doesn't exist
        if not env_file_path.exists():
            env_file_path.touch()
        
        try:
            # Read current content
            if env_file_path.exists():
                with open(env_file_path, 'r', encoding="utf-8") as f:
                    content = f.read()
            else:
                content = ""
            
            # Update or add each key
            keys_to_update = {
                'SUPABASE_JWT_SECRET': jwt_secret,
                'SUPABASE_ANON_KEY': anon_key,
                'SUPABASE_SERVICE_KEY': service_key
            }
            
            updated_content = content
            for key, value in keys_to_update.items():
                # Check if key exists in file
                import re
                pattern = rf'^{re.escape(key)}=.*$'
                replacement = f'{key}={value}'
                
                if re.search(pattern, updated_content, re.MULTILINE):
                    # Key exists, replace it. Lambda bypasses re.sub's
                    # backslash interpretation in the replacement string
                    # (matches the other .env writers — see
                    # source_override_manager.py / service_config.py).
                    updated_content = re.sub(pattern, lambda _m, r=replacement: r, updated_content, flags=re.MULTILINE)
                else:
                    # Key doesn't exist, append it
                    if updated_content and not updated_content.endswith('\n'):
                        updated_content += '\n'
                    updated_content += f'{replacement}\n'
            
            atomic_write_text(env_file_path, updated_content, mode=0o600)

            return True
            
        except Exception as e:
            print(f"❌ Failed to update .env file: {e}")
            return False
    
    def generate_keys(self) -> dict:
        """
        Generate all Supabase keys.
        
        Returns:
            dict: Dictionary containing jwt_secret, anon_key, and service_key
        """
        print("🔐 Generating SUPABASE_JWT_SECRET...")
        jwt_secret = self.generate_jwt_secret()

        print("🔑 Generating SUPABASE_ANON_KEY...")
        anon_key = self.create_jwt_token(jwt_secret, "anon")

        print("🔑 Generating SUPABASE_SERVICE_KEY...")
        service_key = self.create_jwt_token(jwt_secret, "service_role")

        print("✅ Keys generated.")
        
        return {
            'jwt_secret': jwt_secret,
            'anon_key': anon_key,
            'service_key': service_key
        }
    
    def generate_and_update_env(self) -> bool:
        """
        Generate keys and update .env file.
        
        Returns:
            bool: True if successful
        """
        # Generate the keys
        keys = self.generate_keys()
        
        # Update .env file
        if self.update_env_file(keys['jwt_secret'], keys['anon_key'], keys['service_key']):
            print("🔄 .env updated:")
            print("  - SUPABASE_JWT_SECRET")
            print("  - SUPABASE_ANON_KEY")
            print("  - SUPABASE_SERVICE_KEY")
            return True
        else:
            return False
