#!/usr/bin/env python3

import sqlite3

conn = sqlite3.connect('/Users/travisross/Zotero/zotero.sqlite')
conn.row_factory = sqlite3.Row

# Find Goetzmann item
query = '''
SELECT i.itemID, i.key, idv.value as title
FROM items i
JOIN itemData id ON i.itemID = id.itemID
JOIN fields f ON id.fieldID = f.fieldID AND f.fieldName = 'title'
JOIN itemDataValues idv ON id.valueID = idv.valueID
WHERE idv.value LIKE '%Goetzmann%' OR idv.value LIKE '%Money Changes Everything%'
'''

items = conn.execute(query).fetchall()
for item in items:
    print(f'Item {item["itemID"]}: {item["title"]}')
    
    # Get attachments for this item
    att_query = '''
    SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.contentType, ia.path
    FROM itemAttachments ia
    WHERE ia.parentItemID = ?
    '''
    attachments = conn.execute(att_query, (item['itemID'],)).fetchall()
    for att in attachments:
        print(f'  Attachment {att["itemID"]}: linkMode={att["linkMode"]}, path={att["path"]}')

conn.close()