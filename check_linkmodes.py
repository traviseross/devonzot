#!/usr/bin/env python3

import sqlite3

conn = sqlite3.connect('/Users/travisross/Zotero/zotero.sqlite')
conn.row_factory = sqlite3.Row

# Check all linkMode values to understand the mapping
query = '''
SELECT DISTINCT ia.linkMode, COUNT(*) as count
FROM itemAttachments ia
GROUP BY ia.linkMode
ORDER BY ia.linkMode
'''

print("LinkMode values in database:")
for row in conn.execute(query).fetchall():
    print(f'  linkMode {row["linkMode"]}: {row["count"]} attachments')

# Find attachments in ZotFile Import
zotfile_query = '''
SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.path
FROM itemAttachments ia
WHERE ia.path LIKE '%/ZotFile Import/%'
LIMIT 10
'''

print("\nZotFile Import attachments:")
for row in conn.execute(zotfile_query).fetchall():
    print(f'  Attachment {row["itemID"]}: linkMode={row["linkMode"]}, path={row["path"]}')

conn.close()