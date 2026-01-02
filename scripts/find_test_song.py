#!/usr/bin/env python3
"""
Find the test song in the table
"""

import requests
from bs4 import BeautifulSoup

url = "https://www.sop.org/songs/"

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}

print(f"Fetching: {url}")
response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, 'html.parser')

# Find the table
table = soup.find('table')
rows = table.find_all('tr')

# Get headers
header_row = rows[0]
headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]

# Find lyrics column index
lyrics_idx = None
for i, h in enumerate(headers):
    if '歌詞' in h:
        lyrics_idx = i
        break

print(f"Searching for '將天敞開' in {len(rows)} rows...")
print(f"Headers: {headers}")
print(f"Lyrics column index: {lyrics_idx}\n")

# Search all rows for the test song
found = False
for row_num, row in enumerate(rows[1:], 1):
    cells = row.find_all(['td', 'th'])
    if cells:
        song_name = cells[0].get_text(strip=True) if len(cells) > 0 else ''

        if '將天敞開' in song_name or 'Open Heaven' in song_name:
            found = True
            print(f"*** FOUND at row {row_num}: {song_name} ***\n")

            # Show all cell data
            for j, cell in enumerate(cells):
                header_name = headers[j] if j < len(headers) else f"Column {j}"
                cell_text = cell.get_text(separator='\n', strip=True)

                if j == lyrics_idx:  # Lyrics column
                    print(f"\n{header_name} (Column {j}):")
                    print("="*80)

                    # Check for any interactive elements
                    link = cell.find('a')
                    button = cell.find('button')
                    div = cell.find('div')

                    if link:
                        print(f"[LINK] href='{link.get('href')}' text='{link.get_text(strip=True)}'")
                    if button:
                        print(f"[BUTTON] text='{button.get_text(strip=True)}'")
                        # Show all data attributes
                        for attr, value in button.attrs.items():
                            if 'data' in attr or 'class' in attr or 'id' in attr:
                                val_str = str(value)[:500] if isinstance(value, str) else str(value)
                                print(f"  {attr} = {val_str}")
                    if div:
                        print(f"[DIV] class='{div.get('class')}'")

                    # Show raw cell content
                    print(f"\nCell text ({len(cell_text)} chars):")
                    print(cell_text[:1000])

                    print(f"\nCell HTML:")
                    print(str(cell)[:2000])
                    print("="*80)
                else:
                    print(f"  {header_name}: {cell_text[:100]}")

            break

if not found:
    print("\nTest song NOT FOUND in table. Showing all song names containing '敞':")
    for row_num, row in enumerate(rows[1:], 1):
        cells = row.find_all(['td', 'th'])
        if cells:
            song_name = cells[0].get_text(strip=True)
            if '敞' in song_name:
                print(f"  Row {row_num}: {song_name}")
