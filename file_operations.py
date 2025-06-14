"""
File operations for data persistence
"""
import json
import os
from typing import Any, Set
import aiofiles

def load_json_file(filename: str, default: Any = None) -> Any:
    """Load JSON data from file with error handling"""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return default if default is not None else {}
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        return default if default is not None else {}

def save_json_file(filename: str, data: Any) -> bool:
    """Save data to JSON file with error handling"""
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving {filename}: {e}")
        return False

def load_text_file(filename: str) -> Set[str]:
    """Load text file as set of lines"""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return set(line.strip() for line in f if line.strip())
        return set()
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        return set()

def save_text_file(filename: str, data: Set[str]) -> bool:
    """Save set of strings to text file"""
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            for item in sorted(data):
                f.write(f"{item}\n")
        return True
    except Exception as e:
        print(f"Error saving {filename}: {e}")
        return False

async def load_json_file_async(filename: str, default: Any = None) -> Any:
    """Async load JSON data from file"""
    try:
        if os.path.exists(filename):
            async with aiofiles.open(filename, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        return default if default is not None else {}
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        return default if default is not None else {}

async def save_json_file_async(filename: str, data: Any) -> bool:
    """Async save data to JSON file"""
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        return True
    except Exception as e:
        print(f"Error saving {filename}: {e}")
        return False
