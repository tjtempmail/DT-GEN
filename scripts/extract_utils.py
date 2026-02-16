#!/usr/bin/env python3
"""
Advanced ROM extraction utilities for AOSP Device Tree Generator
Handles various ROM formats: payload.bin, sparse, brotli, etc.
"""

import os
import sys
import json
import subprocess
import struct
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

class ROMExtractor:
    """Universal ROM extraction handler"""
    
    SUPPORTED_FORMATS = {
        'zip': ['.zip', '.apk', '.jar'],
        'tar': ['.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz'],
        'lz4': ['.lz4', '.tar.lz4'],
        'br': ['.br', '.tar.br'],
        'sparse': ['.img', '.sparse'],
        'payload': ['payload.bin'],
        'md5': ['.md5'],
    }
    
    def __init__(self, rom_path: str, output_dir: str):
        self.rom_path = Path(rom_path)
        self.output_dir = Path(output_dir)
        self.rom_info = {}
        
    def detect_format(self) -> str:
        """Auto-detect ROM format based on magic bytes and extension"""
        ext = self.rom_path.suffix.lower()
        name = self.rom_path.name.lower()
        
        # Check magic bytes first
        magic = self._get_magic_bytes()
        
        if magic[:4] == b'PK\x03\x04':
            return 'zip'
        elif magic[:4] == b'\x28\xb5\x2f\xfd':
            return 'br'
        elif magic[:4] == b'\x04\x22\x4d\x18':
            return 'lz4'
        elif magic[:7] == b'\x30\x30\x30\x30\x30\x30\x30':
            return 'sparse'
        elif name == 'payload.bin':
            return 'payload'
        elif ext in ['.tar', '.gz', '.bz2', '.xz']:
            return 'tar'
        elif ext == '.md5':
            return 'md5'
            
        # Fallback to extension
        for fmt, exts in self.SUPPORTED_FORMATS.items():
            if ext in exts or any(name.endswith(e) for e in exts):
                return fmt
                
        return 'unknown'
    
    def _get_magic_bytes(self, length: int = 8) -> bytes:
        """Read magic bytes from file"""
        with open(self.rom_path, 'rb') as f:
            return f.read(length)
    
    def extract(self) -> Dict:
        """Extract ROM based on detected format"""
        fmt = self.detect_format()
        self.rom_info['format'] = fmt
        self.rom_info['original_path'] = str(self.rom_path)
        
        print(f"🔍 Detected format: {fmt}")
        
        extractors = {
            'zip': self._extract_zip,
            'tar': self._extract_tar,
            'lz4': self._extract_lz4,
            'br': self._extract_brotli,
            'sparse': self._extract_sparse,
            'payload': self._extract_payload,
            'md5': self._extract_md5,
        }
        
        if fmt in extractors:
            return extractors[fmt]()
        else:
            raise ValueError(f"Unsupported format: {fmt}")
    
    def _extract_zip(self) -> Dict:
        """Extract ZIP archive"""
        import zipfile
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(self.rom_path, 'r') as zf:
            # Check for nested archives
            nested = [f for f in zf.namelist() if f.endswith(('.zip', '.tar', '.md5', '.lz4'))]
            
            print(f"📦 Extracting {len(zf.namelist())} files...")
            zf.extractall(self.output_dir)
            
            # Handle nested archives recursively
            for nest in nested:
                nest_path = self.output_dir / nest
                if nest_path.exists():
                    print(f"🔓 Extracting nested: {nest}")
                    sub_ext = ROMExtractor(str(nest_path), str(self.output_dir / 'nested'))
                    sub_ext.extract()
                    nest_path.unlink()  # Remove nested archive after extraction
        
        return self._scan_output()
    
    def _extract_tar(self) -> Dict:
        """Extract TAR archive"""
        import tarfile
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        with tarfile.open(self.rom_path, 'r:*') as tf:
            print(f"📦 Extracting {len(tf.getmembers())} files...")
            tf.extractall(self.output_dir)
            
        return self._scan_output()
    
    def _extract_lz4(self) -> Dict:
        """Extract LZ4 compressed file"""
        import lz4.frame
        
        output_file = self.output_dir / self.rom_path.stem
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        with open(self.rom_path, 'rb') as f:
            compressed = f.read()
        
        decompressed = lz4.frame.decompress(compressed)
        
        with open(output_file, 'wb') as f:
            f.write(decompressed)
            
        print(f"🔓 Decompressed: {output_file}")
        
        # If result is tar, extract it
        if output_file.suffix in ['.tar']:
            sub_ext = ROMExtractor(str(output_file), str(self.output_dir))
            sub_ext.extract()
            output_file.unlink()
            
        return self._scan_output()
    
    def _extract_brotli(self) -> Dict:
        """Extract Brotli compressed file"""
        import brotli
        
        output_file = self.output_dir / self.rom_path.stem
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        with open(self.rom_path, 'rb') as f:
            compressed = f.read()
        
        decompressed = brotli.decompress(compressed)
        
        with open(output_file, 'wb') as f:
            f.write(decompressed)
            
        print(f"🔓 Decompressed: {output_file}")
        
        # Handle nested archives
        if output_file.suffix in ['.tar', '.zip', '.lz4']:
            sub_ext = ROMExtractor(str(output_file), str(self.output_dir))
            sub_ext.extract()
            output_file.unlink()
            
        return self._scan_output()
    
    def _extract_sparse(self) -> Dict:
        """Convert Android sparse image to raw ext4"""
        output_img = self.output_dir / f"{self.rom_path.stem}.raw.img"
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Use simg2img from android-sdk-libsparse-utils
        result = subprocess.run(
            ['simg2img', str(self.rom_path), str(output_img)],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"simg2img failed: {result.stderr}")
            
        print(f"🔄 Converted sparse image: {output_img}")
        
        # Mount and extract if possible
        self._extract_ext4(output_img)
        
        return self._scan_output()
    
    def _extract_ext4(self, img_path: Path) -> None:
        """Extract ext4 image contents"""
        mount_point = self.output_dir / 'mounted'
        mount_point.mkdir(parents=True, exist_ok=True)
        
        try:
            # Try to mount (requires sudo in most cases)
            subprocess.run(
                ['sudo', 'mount', '-o', 'loop,ro', str(img_path), str(mount_point)],
                check=False
            )
            
            # Copy contents
            import shutil
            for item in mount_point.iterdir():
                dest = self.output_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
                    
        finally:
            # Cleanup
            subprocess.run(['sudo', 'umount', str(mount_point)], check=False)
            mount_point.rmdir()
    
    def _extract_payload(self) -> Dict:
        """Extract payload.bin (A/B OTA)"""
        # Use external payload dumper or aospdtgen's built-in handling
        # This is handled by dumpyara usually
        print("⚠️ payload.bin detected, delegating to dumpyara...")
        return {'format': 'payload', 'delegated': True}
    
    def _extract_md5(self) -> Dict:
        """Handle MD5 checksum files (usually tar.md5)"""
        # Remove .md5 extension and extract the tar
        base_file = self.rom_path.with_suffix('')
        if base_file.exists():
            sub_ext = ROMExtractor(str(base_file), str(self.output_dir))
            return sub_ext.extract()
        else:
            raise FileNotFoundError(f"Expected {base_file} alongside {self.rom_path}")
    
    def _scan_output(self) -> Dict:
        """Scan output directory and return structure info"""
        result = {
            'format': self.rom_info.get('format'),
            'output_dir': str(self.output_dir),
            'files': [],
            'directories': [],
            'build_props': [],
            'partition_images': []
        }
        
        for root, dirs, files in os.walk(self.output_dir):
            for f in files:
                fpath = Path(root) / f
                rel_path = fpath.relative_to(self.output_dir)
                result['files'].append(str(rel_path))
                
                if f == 'build.prop':
                    result['build_props'].append(str(rel_path))
                elif f.endswith('.img'):
                    result['partition_images'].append(str(rel_path))
                    
            for d in dirs:
                dpath = Path(root) / d
                rel_path = dpath.relative_to(self.output_dir)
                result['directories'].append(str(rel_path))
        
        # Save metadata
        metadata_file = self.output_dir / 'extraction_metadata.json'
        with open(metadata_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        return result


def main():
    """CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Advanced ROM Extractor')
    parser.add_argument('rom_path', help='Path to ROM file')
    parser.add_argument('output_dir', help='Output directory')
    parser.add_argument('--format', help='Force specific format')
    
    args = parser.parse_args()
    
    extractor = ROMExtractor(args.rom_path, args.output_dir)
    
    if args.format:
        extractor.rom_info['format'] = args.format
    
    try:
        result = extractor.extract()
        print("\n✅ Extraction complete!")
        print(f"📁 Output: {result['output_dir']}")
        print(f"📊 Files: {len(result['files'])}")
        print(f"🔧 build.prop files: {len(result['build_props'])}")
        print(f"💿 Partition images: {len(result['partition_images'])}")
    except Exception as e:
        print(f"\n❌ Extraction failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
