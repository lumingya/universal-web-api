import urllib.request
import urllib.parse
import ssl
import re
import socket
from datetime import datetime
from bs4 import BeautifulSoup

TARGET_URL = "https://www.ecut.edu.cn"

def check_ssl_certificate(hostname):
    print(f"[*] Checking SSL Certificate for {hostname}...")
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                # Expiry date
                notAfter_str = cert.get('notAfter')
                notAfter = datetime.strptime(notAfter_str, '%b %d %H:%M:%S %Y %Z')
                days_left = (notAfter - datetime.utcnow()).days
                print(f"[+] SSL Expiry Date: {notAfter_str} ({days_left} days left)")
                return {
                    "expiry": notAfter_str,
                    "days_left": days_left,
                    "subject": dict(x[0] for x in cert.get('subject')),
                    "issuer": dict(x[0] for x in cert.get('issuer'))
                }
    except Exception as e:
        print(f"[-] SSL Check Failed: {e}")
        return {"error": str(e)}

def fetch_html(url):
    print(f"[*] Fetching HTML from {url}...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    # Disable SSL verification bypass for diagnostic, but if target has issues we might need it. 
    # Let's try default verification first.
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read()
            return html.decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[-] Fetch Failed: {e}")
        # Try without verifying SSL just in case certificate is expired
        print("[*] Retrying with SSL verification disabled...")
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
                html = response.read()
                return html.decode('utf-8', errors='ignore')
        except Exception as retry_e:
            print(f"[-] Retry Fetch Failed: {retry_e}")
            return None

def analyze_frontend(html, base_url):
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Look for JavaScript and specifically jQuery
    print("[*] Analyzing Script tags...")
    scripts = []
    jquery_version = None
    for script in soup.find_all('script'):
        src = script.get('src')
        if src:
            scripts.append(src)
            # Match jquery files
            match = re.search(r'jquery[.-]([0-9.]+)', src, re.I)
            if match:
                jquery_version = match.group(1)
                print(f"[+] Found jQuery version: {jquery_version} in script: {src}")
    
    # 2. Look for Forms & Inputs
    print("[*] Analyzing Forms and Input security...")
    forms = []
    for form in soup.find_all('form'):
        action = form.get('action')
        method = form.get('method', 'GET').upper()
        # Find password inputs
        pwd_inputs = form.find_all('input', {'type': 'password'})
        inputs = [i.get('name') or i.get('id') for i in form.find_all('input')]
        
        # Check security: is form action HTTP?
        is_secure_action = True
        if action:
            resolved_action = urllib.parse.urljoin(base_url, action)
            if resolved_action.startswith('http://'):
                is_secure_action = False
                
        forms.append({
            "action": action,
            "method": method,
            "has_password": len(pwd_inputs) > 0,
            "inputs": inputs,
            "is_secure_action": is_secure_action
        })
        if len(pwd_inputs) > 0:
            print(f"[!] Warning: Password form found. Method: {method}, Action: {action}, Secure Action: {is_secure_action}")
            
    # 3. Collect Links
    print("[*] Collecting links for broken link check...")
    links = []
    for a in soup.find_all('a'):
        href = a.get('href')
        if href:
            resolved = urllib.parse.urljoin(base_url, href)
            # Skip mailto, javascript, anchor links
            if not resolved.startswith(('mailto:', 'javascript:', '#')):
                links.append(resolved)
                
    # Unique links
    links = list(set(links))
    print(f"[+] Collected {len(links)} unique links.")
    
    return {
        "scripts": scripts,
        "jquery_version": jquery_version,
        "forms": forms,
        "links": links
    }

def test_links(links):
    print("[*] Running broken links detection (testing subset or first 30 links)...")
    broken = []
    tested = 0
    # Test up to 30 links to save time
    for url in links[:30]:
        # Only test http/https links
        if not url.startswith(('http://', 'https://')):
            continue
        tested += 1
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        req = urllib.request.Request(url, headers=headers, method='HEAD')
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                status = response.status
                if status >= 400:
                    broken.append((url, status))
        except Exception as e:
            # Fallback to GET if HEAD is not supported
            req = urllib.request.Request(url, headers=headers, method='GET')
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                    status = response.status
                    if status >= 400:
                        broken.append((url, status))
            except Exception as e2:
                broken.append((url, str(e2)))
                
    print(f"[+] Checked {tested} links. Found {len(broken)} broken/error links.")
    return broken

if __name__ == "__main__":
    hostname = urllib.parse.urlparse(TARGET_URL).hostname
    ssl_info = check_ssl_certificate(hostname)
    html = fetch_html(TARGET_URL)
    if html:
        analysis = analyze_frontend(html, TARGET_URL)
        broken_links = test_links(analysis['links'])
        
        print("\n=== DIAGNOSTIC SUMMARY ===")
        print(f"SSL: {ssl_info.get('expiry')} (Remaining days: {ssl_info.get('days_left')})")
        print(f"jQuery version: {analysis['jquery_version']}")
        print(f"Forms total: {len(analysis['forms'])}")
        for i, f in enumerate(analysis['forms']):
            print(f"  Form {i+1}: Action={f['action']}, Method={f['method']}, Has Password={f['has_password']}, Secure={f['is_secure_action']}")
        print(f"Broken links identified: {len(broken_links)}")
        for link, err in broken_links:
            print(f"  - {link} => {err}")
    else:
        print("[-] Could not retrieve HTML.")
