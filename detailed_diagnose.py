import urllib.request
import urllib.parse
import ssl
import re
import socket
from datetime import datetime
from bs4 import BeautifulSoup

TARGET_URL = "https://www.ecut.edu.cn"

def check_security_headers(url):
    print(f"[*] Checking security headers for {url}...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            resp_headers = response.info()
            security_headers = [
                'Strict-Transport-Security',
                'Content-Security-Policy',
                'X-Frame-Options',
                'X-Content-Type-Options',
                'X-XSS-Protection',
                'Referrer-Policy'
            ]
            results = {}
            for sh in security_headers:
                results[sh] = resp_headers.get(sh, "Missing")
            return results
    except Exception as e:
        return {"error": str(e)}

def fetch_html(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            html = response.read()
            return html.decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[-] Fetch Failed for {url}: {e}")
        return None

def analyze_page(html, url):
    if not html:
        return None
    soup = BeautifulSoup(html, 'html.parser')
    
    # Scripts
    scripts = []
    jquery_info = []
    for s in soup.find_all('script'):
        src = s.get('src')
        if src:
            resolved_src = urllib.parse.urljoin(url, src)
            scripts.append(resolved_src)
            # Try to fetch jquery file to find version if it matches jquery
            if 'jquery' in src.lower():
                jquery_info.append(resolved_src)
                
    # Forms
    forms = []
    for form in soup.find_all('form'):
        action = form.get('action')
        method = form.get('method', 'GET').upper()
        pwd_inputs = form.find_all('input', {'type': 'password'})
        resolved_action = urllib.parse.urljoin(url, action) if action else url
        forms.append({
            "action": resolved_action,
            "method": method,
            "has_password": len(pwd_inputs) > 0,
            "inputs": [i.get('name') or i.get('id') or i.get('type') for i in form.find_all('input')]
        })
        
    # Links
    links = []
    for a in soup.find_all('a'):
        href = a.get('href')
        if href:
            resolved = urllib.parse.urljoin(url, href)
            if resolved.startswith(('http://', 'https://')):
                links.append(resolved)
                
    return {
        "scripts": scripts,
        "jquery_info": jquery_info,
        "forms": forms,
        "links": list(set(links))
    }

def detect_jquery_version_from_content(js_url):
    print(f"[*] Trying to fetch and detect jQuery version from {js_url}...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(js_url, headers=headers)
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            content = response.read().decode('utf-8', errors='ignore')
            # Look for typical jQuery version header in content
            # e.g., jQuery v1.12.4 or jQuery JavaScript Library v3.6.0
            match = re.search(r'jQuery\s+v?([0-9.]+)', content, re.I)
            if match:
                return match.group(1)
            # Another pattern: jQuery = function(...) ... version: "1.12.4"
            match2 = re.search(r'version:\s*["\']([0-9.]+)["\']', content)
            if match2:
                return match2.group(1)
            # Check comments
            match3 = re.search(r'\*\s*v([0-9.]+)', content)
            if match3:
                return match3.group(1)
    except Exception as e:
        print(f"[-] Failed to fetch JS content from {js_url}: {e}")
    return "Unknown"

def test_link_status(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers, method='GET')
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=4) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    # 1. Check Security Headers
    sec_headers = check_security_headers(TARGET_URL)
    
    # 2. Analyze Home Page
    html = fetch_html(TARGET_URL)
    analysis = analyze_page(html, TARGET_URL)
    
    # 3. Detect jQuery Version
    detected_jqs = {}
    for jq_url in analysis['jquery_info']:
        version = detect_jquery_version_from_content(jq_url)
        detected_jqs[jq_url] = version
        print(f"[+] Detected jQuery version for {jq_url}: {version}")
        
    # 4. Search for Login Pages
    login_urls = []
    for link in analysis['links']:
        if any(keyword in link.lower() for keyword in ['login', 'ids', 'portal', 'cas', 'auth', 'signin']):
            login_urls.append(link)
            
    print(f"\n[*] Found {len(login_urls)} potential login/auth links:")
    login_pages_analysis = {}
    for l_url in login_urls[:5]: # check top 5 login URLs
        print(f"  Analyzing Login page: {l_url}")
        l_html = fetch_html(l_url)
        l_analysis = analyze_page(l_html, l_url)
        if l_analysis:
            # Check jQuery inside login pages too
            for jq_url in l_analysis['jquery_info']:
                if jq_url not in detected_jqs:
                    version = detect_jquery_version_from_content(jq_url)
                    detected_jqs[jq_url] = version
            login_pages_analysis[l_url] = l_analysis
            
    # 5. Check a wider list of links (first 80 links)
    print("\n[*] Running broken link check on 80 links...")
    broken_links = []
    tested = 0
    for l in list(analysis['links'])[:80]:
        status = test_link_status(l)
        tested += 1
        if isinstance(status, int):
            if status >= 400:
                broken_links.append((l, status))
        else:
            broken_links.append((l, status))
            
    print(f"\n=== DETAILED REPORT ===")
    print("\n[1] Security Headers:")
    for k, v in sec_headers.items():
        print(f"  {k}: {v}")
        
    print("\n[2] jQuery Versions & Scripts:")
    for jq, ver in detected_jqs.items():
        print(f"  - {jq} => Version: {ver}")
        
    print("\n[3] Form Analysis (Main & Login Pages):")
    # Main forms
    print("  Main Page Forms:")
    for f in analysis['forms']:
        print(f"    - Action: {f['action']}, Method: {f['method']}, Has Password: {f['has_password']}, Inputs: {f['inputs']}")
    # Login page forms
    for l_url, l_an in login_pages_analysis.items():
        print(f"  Login Page Form ({l_url}):")
        for f in l_an['forms']:
            print(f"    - Action: {f['action']}, Method: {f['method']}, Has Password: {f['has_password']}, Inputs: {f['inputs']}")
            
    print(f"\n[4] Broken Links ({len(broken_links)} of {tested} checked):")
    for b_l, err in broken_links:
        print(f"  - {b_l} => {err}")
