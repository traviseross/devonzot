#!/usr/bin/env python3
"""
Test the Zotero attachment replacement functionality
"""

import sys
sys.path.append('/Users/travisross/DEVONzot')
from production_metadata_sync import ZoteroDevonthinkMetadataSync
import sqlite3
from pathlib import Path

def inspect_zotero_attachments():
    """Look at the structure of Zotero attachments to understand the update process"""
    syncer = ZoteroDevonthinkMetadataSync()
    
    try:
        conn = syncer.get_zotero_connection(read_only=True)
        
        # Look at attachment structure
        query = """
        SELECT i.key as parent_key, iv.value as title,
               ia.itemID as attachment_id, ia.path as attachment_path, ia.contentType
        FROM items i
        JOIN itemData id ON i.itemID = id.itemID  
        JOIN itemDataValues iv ON id.valueID = iv.valueID AND id.fieldID = 110  -- title
        JOIN itemAttachments ia ON i.itemID = ia.sourceItemID
        WHERE ia.contentType LIKE '%pdf%'
        AND iv.value IS NOT NULL
        LIMIT 5
        """
        
        results = conn.execute(query).fetchall()
        
        print("📎 Zotero Attachment Structure:")
        print("=" * 50)
        
        for result in results:
            print(f"\nParent Item Key: {result['parent_key']}")
            print(f"Title: {result['title']}")
            print(f"Attachment ID: {result['attachment_id']}")
            print(f"Current Path: {result['attachment_path']}")
            print(f"Content Type: {result['contentType']}")
            print("-" * 30)
        
        conn.close()
        
        if results:
            return results[0]
        else:
            return None
            
    except Exception as e:
        print(f"Error inspecting attachments: {e}")
        return None

def test_zotfile_import_cleanup():
    """Check what's in the ZotFile Import directory"""
    zotfile_dir = Path.home() / "ZotFile Import"
    
    print(f"\n📁 ZotFile Import Directory: {zotfile_dir}")
    print("=" * 50)
    
    if not zotfile_dir.exists():
        print("❌ Directory does not exist")
        return
    
    files = list(zotfile_dir.glob("*"))
    print(f"Found {len(files)} items:")
    
    symlink_count = 0
    for file_path in files[:10]:  # Show first 10
        if file_path.is_symlink():
            symlink_count += 1
            try:
                target = file_path.readlink()
                status = "✅ Valid" if target.exists() else "❌ Broken"
                print(f"  🔗 {file_path.name} → {target} ({status})")
            except Exception as e:
                print(f"  🔗 {file_path.name} → ERROR: {e}")
        else:
            print(f"  📄 {file_path.name}")
    
    if len(files) > 10:
        print(f"  ... and {len(files) - 10} more")
    
    print(f"\nSymlinks: {symlink_count}/{len(files)}")

def demonstrate_workflow():
    """Demonstrate what the complete workflow would do"""
    
    print("🧪 Demonstrating Complete ZotFile Replacement Workflow")
    print("=" * 60)
    
    # 1. Show current Zotero attachments
    sample_attachment = inspect_zotero_attachments()
    
    # 2. Show ZotFile directory
    test_zotfile_import_cleanup()
    
    # 3. Explain what would happen
    print(f"\n🔄 Complete Workflow Process:")
    print(f"=" * 40)
    
    print(f"1️⃣ METADATA SYNC:")
    print(f"   • Extract metadata from Zotero database")
    print(f"   • Generate smart tags (type, publication, decade, themes)")
    print(f"   • Apply tags to DEVONthink records")
    print(f"   • Set macOS native metadata (author, title, description)")
    
    print(f"\n2️⃣ ZOTERO ATTACHMENT UPDATE:")
    if sample_attachment:
        print(f"   • Current: {sample_attachment['attachment_path']}")
        print(f"   • Would become: x-devonthink-item://[UUID]")
        print(f"   • Updates itemAttachments table in Zotero database")
    else:
        print(f"   • No attachments found to demonstrate")
    
    print(f"\n3️⃣ SYMLINK CLEANUP:")
    zotfile_dir = Path.home() / "ZotFile Import"
    if zotfile_dir.exists():
        symlinks = [f for f in zotfile_dir.glob("*") if f.is_symlink()]
        print(f"   • Would check {len(symlinks)} symlinks in ~/ZotFile Import/")
        print(f"   • Remove broken or Zotero-related symlinks")
        print(f"   • Preserve any non-Zotero symlinks")
    else:
        print(f"   • No ZotFile Import directory found")
    
    print(f"\n4️⃣ BACKFILL CHECK:")
    print(f"   • Check DEVONthink for additional tags not from sync")
    print(f"   • Log potential data to sync back to Zotero")
    print(f"   • Store DEVONthink file paths as reference")
    
    print(f"\n💡 RESULT:")
    print(f"   ✅ Mobile workflow enabled (DEVONthink sync works)")
    print(f"   ✅ No more broken symlinks")
    print(f"   ✅ Intelligent archive discovery via tags")
    print(f"   ✅ Native metadata integration")
    
    print(f"\n⚠️  REQUIREMENTS:")
    print(f"   • Zotero must be closed during database updates")
    print(f"   • Backup recommended before first run")
    print(f"   • DEVONthink items must already have UUID links")
    
    print(f"\n🚀 TO RUN:")
    print(f"   python3 production_metadata_sync.py --complete")

if __name__ == "__main__":
    demonstrate_workflow()