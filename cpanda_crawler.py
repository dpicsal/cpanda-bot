import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

def is_internal(url, base_domain):
    return urlparse(url).netloc == base_domain

def get_all_text_from_url(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        return soup.get_text(separator='\n', strip=True)
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return ""

def crawl_site(start_url, max_pages=30):
    base_domain = urlparse(start_url).netloc
    visited = set()
    to_visit = [start_url]
    all_texts = {}

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        print(f"Crawling: {url}")
        visited.add(url)
        text = get_all_text_from_url(url)
        all_texts[url] = text

        # Find new links
        try:
            soup = BeautifulSoup(requests.get(url).text, 'html.parser')
            for link in soup.find_all('a', href=True):
                full_url = urljoin(url, link['href'])
                if is_internal(full_url, base_domain) and full_url not in visited and full_url not in to_visit:
                    to_visit.append(full_url)
        except Exception as e:
            print(f"Error parsing links from {url}: {e}")
    return all_texts

# Usage
start_url = "https://cpanda.app/"
all_page_texts = crawl_site(start_url, max_pages=20)  # Adjust max_pages as needed

# Save to file
with open("cpanda_pages.txt", "w", encoding="utf-8") as f:
    for url, text in all_page_texts.items():
        f.write(f"--- {url} ---\n{text}\n\n")

print("All page texts saved to cpanda_pages.txt")
