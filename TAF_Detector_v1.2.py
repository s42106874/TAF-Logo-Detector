"""
TAF Logo Auto-Detection System v1.2 - Enhanced Crash Protection & Configurable OCR
==================================================================================
針對 Ryzen 5 PRO 4650U + 16GB RAM 優化
- 混合 DPI 策略: 可設定 Low/High DPI
- EasyOCR batch_size 可設定（建議 1~4）
- 每 10 個檔案自動儲存進度
- 加強記憶體監控：11GB 降執行緒、14GB 暫停 30s
- 批次結束強制清空 CUDA 快取 + GC
- 全域異常捕捉 + 崩潰自動儲存恢復點
作者: TAF System Team (強化版)
版本: 1.2 - Crash Safe + Configurable OCR + Fixed Save Paths
日期: 2025
Python: 3.8+
"""
# ============================================================================
# 標準庫導入
# ============================================================================
# ============================================================================
# 標準庫導入
# ============================================================================
import os
import sys
import time
import re
import json
import threading
import shutil
from typing import Dict, List, Optional, Tuple, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc
import ctypes  # 防休眠用
import tkinter as tk
from tkinter import ttk, messagebox, Toplevel

# ============================================================================
# 延遲導入的模組 (将在 startup 中載入)
# ============================================================================
psutil = None
fitz = None
pd = None
np = None
cv2 = None
Image = None
load_workbook = None
PatternFill = None
Font = None
Alignment = None
Border = None
Side = None
easyocr = None
torch = None

HAS_EASYOCR = False

# ============================================================================
# 全域變數 - EasyOCR Reader
# ============================================================================
EASYOCR_READER = None

# ============================================================================
# 防休眠工具
# ============================================================================
def simulate_numlock_toggle():
    """模擬按下並放開 Num Lock 鍵，防止系統休眠"""
    try:
        user32 = ctypes.windll.user32
        VK_NUMLOCK = 0x90
        user32.keybd_event(VK_NUMLOCK, 0, 0, 0)  # 按下
        user32.keybd_event(VK_NUMLOCK, 0, 2, 0)  # 放開
    except Exception as e:
        print(f"[ANTI-SLEEP] 模擬 Num Lock 失敗: {e}")

class AntiSleep:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("[ANTI-SLEEP] 後台防休眠已啟動（每30秒模擬 Num Lock）")

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        print("[ANTI-SLEEP] 防休眠已停止")

    def _run(self):
        while self.running:
            simulate_numlock_toggle()
            time.sleep(30)

# ============================================================================
# 工具函式
# ============================================================================
def resource_path(relative_path: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def get_visual_width(text: str) -> float:
    if not text:
        return 0.0
    text = str(text)
    width = 0.0
    for char in text:
        if '\u4e00' <= char <= '\u9fff' or '\uff01' <= char <= '\uff5e':
            width += 2.0
        else:
            width += 1.0
    return width

def release_system_memory():
    """強制釋放 Python 與 PyTorch 快取記憶體"""
    gc.collect()
    if HAS_EASYOCR:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except:
            pass

# ============================================================================
# 全域異常捕捉 + 自動儲存恢復點
# ============================================================================
def global_exception_handler(exc_type, exc_value, exc_traceback):
    import traceback
    print("[CRASH HANDLER] 程式崩潰！")
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    
    # 嘗試儲存目前結果
    try:
        if 'all_results' in globals() and all_results:
            crash_path = os.path.join(Config.get_app_dir(), Config.CRASH_RECOVERY_FILE)
            ExcelReporter.export_report(all_results, crash_path)
            print(f"[CRASH] 已儲存恢復點到 {crash_path}")
    except Exception as e:
        print(f"[CRASH] 無法儲存恢復點: {e}")
    
    sys.exit(1)

sys.excepthook = global_exception_handler

# ============================================================================
# 配置類（新增 OCR 參數）
# ============================================================================
class Config:
    DOMAIN_MAPPING_FILE = "DomainMapping.xlsx"
    OUTPUT_REPORT_FILE = "Full_Report.xlsx"
    AUTOSAVE_FILE = "AutoSave_Progress.xlsx"
    CRASH_RECOVERY_FILE = "Crash_Recovery_Report.xlsx"
    CONFIG_FILE = "taf_config.json"

    # OCR 參數（預設值，可從 config 覆蓋）
    OCR_DPI_LOW = 200
    OCR_DPI_HIGH = 250
    OCR_BATCH_SIZE = 1  # EasyOCR batch_size，建議 1~4

    OCR_ALLOWLIST = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz -.:'

    DEFAULT_TAF_NUMBERS = {
        "1137": {
            "domain": "Y",
            "keywords": ["civilengineeringlaboratory", "civil", "engineering", "civillaboratory", "civil lab", "civil engineering"]
        },
        "0292": {
            "domain": "Z",
            "keywords": ["testinglaboratory", "testing", "testlab", "testinglab"]
        },
        "10037": {
            "domain": "I",
            "keywords": ["inspectionbody", "inspection", "body", "inspectionlab", "inspectionbureau"]
        },
        "I0037": {
            "domain": "I",
            "keywords": ["inspectionbody", "inspection", "body", "inspectionlab", "inspectionbureau"]
        },
        "2076": {
            "domain": "Y",
            "keywords": ["civilengineeringlaboratory", "civil", "engineering", "civillaboratory", "engineeringlaboratory"]
        }
    }
    TAF_PRIORITY_LIST = ["1137", "2076", "0292", "10037", "I0037"]
    DOMAIN_GROUPS = {
        "Y": ["1137", "2076"],
        "Z": ["0292"],
        "I": ["10037", "I0037"]
    }
    HEADER_FILL = None
    CORRECT_FILL = None
    ERROR_FILL = None
    SKIP_FILL = None
    FORMAT_ERROR_FILL = None
    WHITE_FILL = None
    HEADER_FONT = None
    WHITE_FONT = None
    BLACK_FONT = None
    ALIGN_HEADER = None
    ALIGN_CENTER_DATA = None
    ALIGN_LEFT_DATA = None
    THIN_BORDER = None

    @classmethod
    def init_styles(cls):
        """延遲初始化 Excel 樣式"""
        if cls.HEADER_FILL is not None:
            return
        
        try:
            cls.HEADER_FILL = PatternFill(start_color="004a99", end_color="004a99", fill_type="solid")
            cls.CORRECT_FILL = PatternFill(start_color="28a745", end_color="28a745", fill_type="solid")
            cls.ERROR_FILL = PatternFill(start_color="dc3545", end_color="dc3545", fill_type="solid")
            cls.SKIP_FILL = PatternFill(start_color="6c757d", end_color="6c757d", fill_type="solid")
            cls.FORMAT_ERROR_FILL = PatternFill(start_color="ffc107", end_color="ffc107", fill_type="solid")
            cls.WHITE_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
            cls.HEADER_FONT = Font(color="FFFFFF", bold=True, size=12)
            cls.WHITE_FONT = Font(color="FFFFFF", bold=False, size=11)
            cls.BLACK_FONT = Font(color="000000", bold=False, size=11)
            cls.ALIGN_HEADER = Alignment(horizontal="center", vertical="center", wrap_text=False)
            cls.ALIGN_CENTER_DATA = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cls.ALIGN_LEFT_DATA = Alignment(horizontal="left", vertical="center", wrap_text=True)
            cls.THIN_BORDER = Border(
                left=Side('thin'),
                right=Side('thin'),
                top=Side('thin'),
                bottom=Side('thin')
            )
        except Exception as e:
            print(f"[WARNING] 樣式初始化失敗: {e}")
    @staticmethod
    def get_app_dir() -> str:
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_max_workers() -> int:
        try:
            logical_cores = os.cpu_count() or 4
            available_mem_gb = psutil.virtual_memory().available / (1024 ** 3)
            suggested = min(12, max(1, int(logical_cores * 0.7)))
            if available_mem_gb < 4:
                suggested = min(suggested, 4)
                print(f"[MEM LOW] 可用記憶體 {available_mem_gb:.1f} GB，強制執行緒數降至 {suggested}")
            else:
                print(f"[MEM OK] 可用記憶體 {available_mem_gb:.1f} GB，允許 {suggested} 執行緒")
            print(f"[AUTO] 偵測到 {logical_cores} 邏輯處理器，自動設定執行緒數: {suggested}")
            return suggested
        except Exception as e:
            print(f"[AUTO WARN] 無法偵測 CPU/記憶體，使用預設 4 執行緒: {e}")
            return 4

# ============================================================================
# 配置管理器（新增 OCR 參數讀寫）
# ============================================================================
class ConfigManager:
    @staticmethod
    def load_config() -> Dict:
        config_path = os.path.join(Config.get_app_dir(), Config.CONFIG_FILE)
        if not os.path.exists(config_path):
            print(f"[INFO] 配置檔不存在於 {config_path}，使用預設值")
            return ConfigManager._get_default_config()
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            Config.OCR_DPI_LOW = data.get("ocr_dpi_low", 200)
            Config.OCR_DPI_HIGH = data.get("ocr_dpi_high", 250)
            Config.OCR_BATCH_SIZE = data.get("ocr_batch_size", 1)
            taf_numbers = {}
            loaded_data = data.get("taf_numbers", {})
            if not loaded_data:
                loaded_data = Config.DEFAULT_TAF_NUMBERS.copy()
            for num, entry in loaded_data.items():
                domain = entry.get("domain", "X").upper()
                keywords = entry.get("keywords", [])
                taf_numbers[num] = {"domain": domain, "keywords": keywords}
            for num, data_entry in taf_numbers.items():
                if "keywords" not in data_entry or not data_entry["keywords"]:
                    if num in Config.DEFAULT_TAF_NUMBERS:
                        data_entry["keywords"] = Config.DEFAULT_TAF_NUMBERS[num]["keywords"]
                    else:
                        data_entry["keywords"] = ["taf"]
            return {
                "taf_numbers": taf_numbers,
                "max_workers": data.get("max_workers", Config.get_max_workers()),
                "scan_mode": data.get("scan_mode", "scan_to_first"),
                "theme": data.get("theme", "light"),
                "ocr_dpi_low": Config.OCR_DPI_LOW,
                "ocr_dpi_high": Config.OCR_DPI_HIGH,
                "ocr_batch_size": Config.OCR_BATCH_SIZE
            }
        except Exception as e:
            print(f"[ERROR] 配置載入失敗: {e}")
            print(f"[INFO] 使用預設配置")
            return ConfigManager._get_default_config()

    @staticmethod
    def save_config(config: Dict) -> bool:
        try:
            config_path = os.path.join(Config.get_app_dir(), Config.CONFIG_FILE)
            clean_config = {
                "taf_numbers": config["taf_numbers"],
                "max_workers": config["max_workers"],
                "scan_mode": config["scan_mode"],
                "theme": config.get("theme", "light"),
                "ocr_dpi_low": config.get("ocr_dpi_low", 150),
                "ocr_dpi_high": config.get("ocr_dpi_high", 200),
                "ocr_batch_size": config.get("ocr_batch_size", 1)
            }
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(clean_config, f, ensure_ascii=False, indent=4)
            print(f"[SUCCESS] 配置已儲存至 {config_path}")
            return True
        except Exception as e:
            print(f"[ERROR] 配置儲存失敗: {e}")
            return False

    @staticmethod
    def _get_default_config() -> Dict:
        return {
            "taf_numbers": Config.DEFAULT_TAF_NUMBERS.copy(),
            "max_workers": Config.get_max_workers(),
            "scan_mode": "scan_to_first",
            "theme": "light",
            "ocr_dpi_low": 200,
            "ocr_dpi_high": 250,
            "ocr_batch_size": 1
        }

# ============================================================================
# 影像處理模組
# ============================================================================
class ImageProcessor:
    @staticmethod
    def preprocess(pil_img, mode: str = "standard"):
        if mode == "no_preprocess":
            return pil_img
        try:
            arr = np.array(pil_img)
            if len(arr.shape) == 3:
                gray = np.dot(arr[..., :3], [0.299, 0.587, 0.114]).astype(np.uint8)
            else:
                gray = arr
            if mode == "standard":
                denoised = cv2.medianBlur(gray, 3)
                thresh = cv2.adaptiveThreshold(
                    denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY, 11, 2
                )
                result = Image.fromarray(thresh)
            elif mode == "aggressive":
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                result = Image.fromarray(thresh)
            else:
                result = Image.fromarray(gray)
            del arr, gray, denoised, thresh
            release_system_memory()
            return result
        except Exception as e:
            print(f"[WARNING] 影像預處理失敗: {e}")
            return pil_img

# ============================================================================
# Logo 識別模組
# ============================================================================
class LogoDetector:
    @staticmethod
    def find_logos_from_text(text: str, taf_numbers: dict) -> Tuple[Set[str], Set[str]]:
        if not text:
            return set(), set()
        normalized_text = text.lower()
        normalized_text = (
            normalized_text
            .replace('civl', 'civil')
            .replace('c1v1', 'civil')
            .replace('civi1', 'civil')
            .replace('eng1neer1ng', 'engineering')
            .replace('eng1neer', 'engineer')
            .replace('laborat0ry', 'laboratory')
        )
        clean_text = ''.join(normalized_text.split())
        known_logos = set()
        unknown_logos = set()
        for num, data in taf_numbers.items():
            keywords = data.get("keywords", [])
            match_keyword = False
            if not keywords:
                match_keyword = True
            else:
                for kw in keywords:
                    kw_lower = kw.lower()
                    if len(kw_lower) >= 3 and kw_lower[:3] in clean_text:
                        match_keyword = True
                        break
                    if kw_lower in clean_text:
                        match_keyword = True
                        break
            if not match_keyword:
                continue
            num_corrected_text = LogoDetector._correct_ocr_errors(normalized_text)
            num_to_find = num.upper().replace('I', '1').replace('L', '1').replace('O', '0')
            if num_to_find in num_corrected_text:
                known_logos.add(num)
        matches = re.findall(r'taf\s*(\d{4})', normalized_text)
        for num in matches:
            if num not in taf_numbers and num.isdigit():
                unknown_logos.add(num)
        return known_logos, unknown_logos

    @staticmethod
    def _correct_ocr_errors(text: str) -> str:
        corrections = {'i': '1', 'l': '1', '|': '1', 'o': '0', 'z': '2', 'g': '9', 's': '5', 'b': '8'}
        result = text
        for old, new in corrections.items():
            result = result.replace(old, new)
        return result

    @staticmethod
    def find_logos_from_text_layer(page, taf_numbers: dict) -> Tuple[Set[str], Set[str]]:
        known_logos = set()
        unknown_logos = set()
        try:
            text_data = page.get_text("dict", flags=fitz.TEXTFLAGS_SEARCH)
            full_text = []
            for block in text_data.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        full_text.append(span["text"])
            if full_text:
                full_text_str = " ".join(full_text)
                print(f"[DEBUG] Text layer content: {full_text_str}")
                known, unknown = LogoDetector.find_logos_from_text(full_text_str, taf_numbers)
                known_logos.update(known)
                unknown_logos.update(unknown)
        except Exception as e:
            print(f"[WARNING] 文字層提取失敗: {e}")
        return known_logos, unknown_logos

# ============================================================================
# PDF 處理模組
# ============================================================================
class PDFProcessor:
    @staticmethod
    def get_scan_regions(page) -> List:
        rect = page.rect
        w, h = rect.width, rect.height
        # 修改為：掃描整個頁面上方 30% (涵蓋左上、中上、右上)
        return [fitz.Rect(0, 0, w, h * 0.3)]

    @staticmethod
    def process_pdf(args: Tuple) -> Optional[Dict]:
        pdf_path, filename, report_num, expected_domain, taf_numbers, scan_mode, stop_event = args
        if stop_event.is_set():
            return None
        result = {
            "報告編號": report_num,
            "檔案名稱": filename,
            "應有領域別": expected_domain,
            "檢測PDF領域別": None,
            "掃描Logo編號": "未找到",
            "判別結果": "待檢查",
            "錯誤說明": ""
        }
        if expected_domain == "W":
            result.update({
                "檢測PDF領域別": "X",
                "掃描Logo編號": "未掃描",
                "判別結果": "跳過",
                "錯誤說明": "對照表為 W"
            })
            return result
        doc = None
        try:
            doc = fitz.open(pdf_path)
            known_found_numbers = set()
            unknown_found_numbers = set()
            pages_to_scan = len(doc)
            for page_num in range(pages_to_scan):
                if stop_event.is_set():
                    break
                page = doc[page_num]
                known, unknown = LogoDetector.find_logos_from_text_layer(page, taf_numbers)
                known_found_numbers.update(known)
                unknown_found_numbers.update(unknown)
                if not known_found_numbers:
                    PDFProcessor._scan_with_easyocr(
                        page, page_num, filename, taf_numbers,
                        known_found_numbers, unknown_found_numbers, stop_event
                    )
                if scan_mode == "scan_to_first" and known_found_numbers:
                    break
            return PDFProcessor._build_result(
                result, known_found_numbers, unknown_found_numbers, taf_numbers, expected_domain, scan_mode
            )
        except Exception as e:
            if not stop_event.is_set():
                result.update({
                    "檢測PDF領域別": "錯誤",
                    "掃描Logo編號": "ERROR",
                    "判別結果": "錯誤",
                    "錯誤說明": f"系統錯誤: {str(e)}"
                })
                return result
        finally:
            if doc is not None:
                doc.close()

    @staticmethod
    def _scan_with_easyocr(page, page_num, filename, taf_numbers,
                           known_found_numbers, unknown_found_numbers, stop_event):
        global EASYOCR_READER
        if EASYOCR_READER is None or stop_event.is_set():
            return
        regions = PDFProcessor.get_scan_regions(page)
        for clip_rect in regions:
            if stop_event.is_set():
                break
            try:
                pix = page.get_pixmap(dpi=Config.OCR_DPI_LOW, clip=clip_rect)
                if pix.width < 100 or pix.height < 100:
                    continue
                img_pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                img_array = np.array(img_pil)
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                ocr_results = EASYOCR_READER.readtext(
                    img_array,
                    detail=0,
                    paragraph=False,
                    contrast_ths=0.2,
                    adjust_contrast=0.6,
                    mag_ratio=1.0,
                    batch_size=Config.OCR_BATCH_SIZE
                )
                full_text = " ".join(ocr_results) if ocr_results else ""
                print(f"[DEBUG] OCR low DPI content ({filename} page {page_num+1}): {full_text}")
                known, unknown = LogoDetector.find_logos_from_text(full_text, taf_numbers)
                if known or unknown:
                    if known:
                        known_found_numbers.update(known)
                    if unknown:
                        unknown_found_numbers.update(unknown)
                else:
                    print(f"[INFO] {filename} 頁 {page_num+1}: {Config.OCR_DPI_LOW} DPI 未找到，升到 {Config.OCR_DPI_HIGH} DPI 重試")
                    pix = page.get_pixmap(dpi=Config.OCR_DPI_HIGH, clip=clip_rect)
                    img_pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    img_array = np.array(img_pil)
                    img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                    ocr_results = EASYOCR_READER.readtext(
                        img_array,
                        detail=0,
                        paragraph=False,
                        contrast_ths=0.2,
                        adjust_contrast=0.6,
                        mag_ratio=1.0,
                        batch_size=Config.OCR_BATCH_SIZE
                    )
                    full_text = " ".join(ocr_results) if ocr_results else ""
                    print(f"[DEBUG] OCR high DPI content ({filename} page {page_num+1}): {full_text}")
                    known, unknown = LogoDetector.find_logos_from_text(full_text, taf_numbers)
                    if known:
                        known_found_numbers.update(known)
                    if unknown:
                        unknown_found_numbers.update(unknown)
                del pix, img_pil, img_array, ocr_results
                release_system_memory()
            except Exception as e:
                print(f"[ERROR] {filename} 頁 {page_num+1} OCR 失敗: {e}")

    @staticmethod
    def _build_result(result, known_found, unknown_found, taf_numbers, expected_domain, scan_mode):
        known_numbers_only = known_found.copy()
        
        # 1. 領域判斷用的邏輯 (需保留優先順序過濾)
        detection_set = known_numbers_only.copy()
        found_Y = any(num in detection_set for num in Config.DOMAIN_GROUPS["Y"])
        if found_Y:
            for num in Config.DOMAIN_GROUPS["Z"]:
                detection_set.discard(num)
            for num in Config.DOMAIN_GROUPS["I"]:
                detection_set.discard(num)
        else:
            found_Z = any(num in detection_set for num in Config.DOMAIN_GROUPS["Z"])
            if found_Z:
                for num in Config.DOMAIN_GROUPS["I"]:
                    detection_set.discard(num)
        
        # 用於判斷的集合
        decision_numbers = detection_set.union(unknown_found)

        # 2. 顯示用的邏輯 (全掃描模式下顯示所有找到的若是 scan_to_first 則維持過濾後的結果)
        if scan_mode == "full_scan":
            display_numbers = known_found.union(unknown_found)
        else:
            display_numbers = decision_numbers

        if not decision_numbers:
            # 如果連判斷用的都沒有，那就真的沒有
            detected_domain = "X"
            if not display_numbers:
                logo_number = "未找到"
            else:
                # 雖然被過濾掉了導致判斷為 X，但如果有掃到東西顯示出來
                filtered_numbers = [n for n in display_numbers if (n.isdigit() and len(n) == 4) or (n.startswith('I') and n[1:].isdigit())]
                logo_number = " | ".join(sorted(filtered_numbers))
        else:
            primary_logo = None
            for num in Config.TAF_PRIORITY_LIST:
                if num in decision_numbers:
                    primary_logo = num
                    break
            if not primary_logo and detection_set:
                primary_logo = sorted(detection_set)[0]
            
            if primary_logo:
                detected_domain = taf_numbers.get(primary_logo, {}).get("domain", "X")
            else:
                detected_domain = "X"
                if unknown_found:
                    primary_logo = sorted(unknown_found)[0]
            
            filtered_numbers = [n for n in display_numbers if (n.isdigit() and len(n) == 4) or (n.startswith('I') and n[1:].isdigit())]
            logo_number = " | ".join(sorted(filtered_numbers))

        result["檢測PDF領域別"] = detected_domain
        result["掃描Logo編號"] = logo_number
        if expected_domain == "R":
            result["判別結果"] = "正確" if detected_domain == "X" else "錯誤"
        else:
            result["判別結果"] = "正確" if detected_domain == expected_domain else "錯誤"
        if result["判別結果"] == "錯誤":
            result["錯誤說明"] = f"領域別不符 (應: {expected_domain}, 實: {detected_domain})"
        return result

# ============================================================================
# Excel 報告生成器
# ============================================================================
class ExcelReporter:
    @staticmethod
    def export_report(results: List[Dict], output_path: str) -> bool:
        try:
            if not results:
                print("[WARNING] 沒有結果可匯出")
                return True
            if os.path.exists(output_path):
                os.remove(output_path)
            df = pd.DataFrame(results).sort_values('報告編號', na_position='first')
            df.to_excel(output_path, index=False, engine='openpyxl')
            ExcelReporter.format_excel(output_path)
            print(f"[SUCCESS] 報告已匯出至 {output_path}")
            return True
        except PermissionError:
            print(f"[ERROR] 權限錯誤：{os.path.basename(output_path)} 可能正在使用中")
            return False
        except Exception as e:
            print(f"[ERROR] 匯出失敗: {e}")
            return False

    @staticmethod
    def format_excel(file_path: str) -> None:
        try:
            wb = load_workbook(file_path)
            ws = wb.active
            ExcelReporter._format_header_row(ws)
            result_col_letter = ExcelReporter._get_result_column(ws)
            ExcelReporter._format_data_rows(ws, result_col_letter)
            ExcelReporter._adjust_column_widths(ws)
            wb.save(file_path)
            print(f"[SUCCESS] Excel 格式化完成")
        except Exception as e:
            print(f"[WARNING] Excel 格式化失敗: {e}")

    @staticmethod
    def _format_header_row(ws):
        for cell in ws[1]:
            cell.fill = Config.HEADER_FILL
            cell.font = Config.HEADER_FONT
            cell.alignment = Config.ALIGN_HEADER
            cell.border = Config.THIN_BORDER

    @staticmethod
    def _get_result_column(ws) -> Optional[str]:
        for cell in ws[1]:
            if cell.value == "判別結果":
                return cell.column_letter
        return None

    @staticmethod
    def _format_data_rows(ws, result_col_letter: Optional[str]):
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            result_value = ""
            if result_col_letter:
                result_value = ws[f"{result_col_letter}{row[0].row}"].value or ""
            fill = ExcelReporter._get_fill_color(result_value)
            for cell in row:
                cell.border = Config.THIN_BORDER
                if cell.column_letter == result_col_letter:
                    cell.alignment = Config.ALIGN_CENTER_DATA
                    cell.fill = fill
                    cell.font = Config.WHITE_FONT
                else:
                    cell.alignment = Config.ALIGN_LEFT_DATA
                    cell.fill = Config.WHITE_FILL
                    cell.font = Config.BLACK_FONT

    @staticmethod
    def _get_fill_color(result: str) -> PatternFill:
        if "正確" in result:
            return Config.CORRECT_FILL
        elif "跳過" in result:
            return Config.SKIP_FILL
        elif "N/A" in result:
            return Config.FORMAT_ERROR_FILL
        else:
            return Config.ERROR_FILL

    @staticmethod
    def _adjust_column_widths(ws):
        for col in ws.columns:
            col_letter = col[0].column_letter
            max_width = 0
            for cell in col:
                width = get_visual_width(cell.value)
                if width > max_width:
                    max_width = width
            ws.column_dimensions[col_letter].width = max_width + 5

# ============================================================================
# 啟動與載入邏輯
# ============================================================================
def load_heavy_dependencies(status_callback=None):
    global psutil, fitz, pd, np, cv2, Image, load_workbook, PatternFill, Font, Alignment, Border, Side
    global easyocr, torch, HAS_EASYOCR, EASYOCR_READER

    if status_callback: status_callback("正在載入核心模組: psutil...")
    import psutil

    if status_callback: status_callback("正在載入 PDF 引擎: PyMuPDF...")
    import fitz

    if status_callback: status_callback("正在載入數據分析庫: Pandas...")
    import pandas as pd

    if status_callback: status_callback("正在載入數值運算庫: Numpy...")
    import numpy as np

    if status_callback: status_callback("正在載入影像處理庫: OpenCV...")
    import cv2

    if status_callback: status_callback("正在載入影像工具: PIL...")
    from PIL import Image

    if status_callback: status_callback("正在載入 Excel 工具: OpenPyXL...")
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    
    # 初始化 Config 樣式
    Config.init_styles()

    if status_callback: status_callback("正在載入 AI 引擎: PyTorch & EasyOCR (這可能需要幾秒)...")
    try:
        import easyocr
        import torch
        import warnings
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message="'pin_memory' argument is set as true but no accelerator is found"
        )
        HAS_EASYOCR = True
        
        # 預先初始化 EasyOCR
        use_gpu = torch.cuda.is_available()
        
        # 優先檢查 EXE 同目錄下的 models 資料夾 (持久化存儲)
        external_model_path = os.path.join(Config.get_app_dir(), "models")
        if os.path.exists(external_model_path):
            model_path = external_model_path
            if status_callback: status_callback(f"使用外部模型路徑: {model_path}")
        else:
            # 否則使用內部/臨時路徑
            model_path = resource_path(os.path.join("_internal", "models"))
        
        os.makedirs(model_path, exist_ok=True)
        
        if status_callback: status_callback(f"正在初始化 EasyOCR 模型 ({'GPU' if use_gpu else 'CPU'})...")
        EASYOCR_READER = easyocr.Reader(
            ['en'],
            gpu=use_gpu,
            model_storage_directory=model_path
        )
    except ImportError:
        print("[ERROR] EasyOCR 或 Torch 未安裝！")
        HAS_EASYOCR = False
    except Exception as e:
        print(f"[ERROR] EasyOCR 初始化失敗: {e}")
        HAS_EASYOCR = False

def startup():
    # 建立臨時 Root 用於 Splash
    splash_root = tk.Tk()
    splash_root.overrideredirect(True)
    splash_root.withdraw()
    
    # 建立 Splash 視窗
    width, height = 500, 400
    x = (splash_root.winfo_screenwidth() - width) // 2
    y = (splash_root.winfo_screenheight() - height) // 2
    
    splash = Toplevel(splash_root)
    splash.geometry(f"{width}x{height}+{x}+{y}")
    splash.overrideredirect(True)
    splash.configure(bg="#0056b3")
    splash.attributes('-topmost', True)

    # Splash UI 內容
    tk.Label(splash, text="TAF", font=("Segoe UI", 56, "bold"), 
             fg="white", bg="#0056b3").pack(pady=(40, 5))
    tk.Label(splash, text="標章自動檢測系統", font=("微軟正黑體", 20), 
             fg="white", bg="#0056b3").pack()
    tk.Label(splash, text="v1.2 Crash Safe (Optimized)", font=("Segoe UI", 11), 
             fg="#f0f0f0", bg="#0056b3").pack(pady=(5, 30))
    
    style = ttk.Style()
    style.theme_use('clam')
    style.configure("Modern.Horizontal.TProgressbar", background="#28a745", 
                    troughcolor="#dfe6e9", borderwidth=0)
    
    progress = ttk.Progressbar(splash, mode='indeterminate', length=400,
                               style="Modern.Horizontal.TProgressbar")
    progress.pack(pady=20)
    progress.start(10)
    
    status_label = tk.Label(splash, text="正在初始化...", fg="white", 
                            bg="#0056b3", font=("微軟正黑體", 10))
    status_label.pack()

    def update_status(text):
        splash.after(0, lambda: status_label.config(text=text))

    def worker():
        try:
            load_heavy_dependencies(update_status)
        except Exception as e:
            update_status(f"載入失敗: {e}")
            print(f"[FATAL] Startup Error: {e}")
            time.sleep(3)
        
        splash.after(1000, finish)

    def finish():
        try:
            progress.stop()
            splash.update()
        except:
            pass
        splash.destroy()
        splash_root.quit()  # 退出 mainloop
        splash_root.destroy() # 銷毀視窗資源
        # 啟動主程式
        main()

    threading.Thread(target=worker, daemon=True).start()
    splash_root.mainloop()

# ============================================================================
# 主程式 - GUI 部分
# ============================================================================
def main():
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox, Toplevel, END

    class TAFCheckerApp:
        COLORS = {
            "light": {
                "bg": "#f5f5f5", "fg": "#2d2d2d", "surface": "#ffffff",
                "primary": "#0056b3", "success": "#28a745", "error": "#dc3545",
                "warning": "#fdcb6e", "border": "#dfe6e9",
                "log_bg": "#343a40", "log_fg": "#f8f9fa"
            },
            "dark": {
                "bg": "#1e1e1e", "fg": "#d4d4d4", "surface": "#252526",
                "primary": "#007bff", "success": "#10b981", "error": "#ef4444",
                "warning": "#f59e0b", "border": "#3e3e42",
                "log_bg": "#1e1e1e", "log_fg": "#d4d4d4"
            }
        }

        def __init__(self, root: tk.Tk):
            self.root = root
            self.root.title("TAF 標章自動檢測系統 v1.2 - Crash Safe")
            self.root.geometry("1200x800")
            self.root.minsize(1000, 700)
            self.config = ConfigManager.load_config()
            self.taf_numbers = self.config["taf_numbers"]
            self.max_workers = self.config["max_workers"]
            self.scan_mode = self.config["scan_mode"]
            self.theme = self.config.get("theme", "light")
            self.ocr_dpi_low = self.config.get("ocr_dpi_low", 200)
            self.ocr_dpi_high = self.config.get("ocr_dpi_high", 250)
            self.ocr_batch_size = self.config.get("ocr_batch_size", 1)
            self.folder_path = ""
            self.total_files = 0
            self.stop_event = None
            self.executor = None
            self.anti_sleep = AntiSleep()
            # 移除內部 EasyOCR 初始化與 Splash 呼叫 (移至外部 startup)
            # self.init_easyocr_reader()
            self._setup_styles()
            self._setup_ui()
            # self._show_splash()
            self.memory_monitor_id = None
            self._start_memory_monitor()
            self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        def init_easyocr_reader(self):
            global EASYOCR_READER
            try:
                model_path = resource_path(os.path.join("_internal", "models"))
                os.makedirs(model_path, exist_ok=True)
                use_gpu = torch.cuda.is_available()
                print(f"[INIT] 初始化 EasyOCR ({'GPU' if use_gpu else 'CPU'} 模式) - 模型路徑: {model_path}")
                EASYOCR_READER = easyocr.Reader(
                    ['en'],
                    gpu=use_gpu,
                    model_storage_directory=model_path
                )
                print("[SUCCESS] EasyOCR 載入完成")
            except Exception as e:
                print(f"[FATAL] 無法載入 EasyOCR 模型: {e}")
                global HAS_EASYOCR
                HAS_EASYOCR = False

        def _start_memory_monitor(self):
            try:
                process = psutil.Process()
                mem_mb = process.memory_info().rss / (1024 ** 2)
                if mem_mb > 11000:
                    print(f"[MEM CRITICAL] {mem_mb:.1f} MB → 強制 GC + 降低執行緒")
                    release_system_memory()
                    if self.max_workers > 2:
                        self.max_workers = max(2, self.max_workers - 1)
                        self._log(f"記憶體過高，執行緒降至 {self.max_workers}")
                if mem_mb > 14000:
                    self._log("[MEM EMERGENCY] 記憶體極高，暫停 30 秒...")
                    time.sleep(30)
                    release_system_memory()
            except:
                pass
            self.memory_monitor_id = self.root.after(3000, self._start_memory_monitor)

        def _setup_styles(self):
            colors = self.COLORS[self.theme]
            self.root.configure(bg=colors["bg"])
            style = ttk.Style()
            style.theme_use('clam')
            style.configure("Primary.TButton", background=colors["primary"], foreground="white",
                            borderwidth=0, focuscolor='none', padding=12, font=("微軟正黑體", 10, "bold"))
            style.map("Primary.TButton", background=[('active', '#004a99')])
            style.configure("Success.TButton", background=colors["success"], foreground="white",
                            borderwidth=0, padding=10, font=("微軟正黑體", 9))
            style.map("Success.TButton", background=[('active', '#218838')])
            style.configure("Danger.TButton", background=colors["error"], foreground="white",
                            borderwidth=0, padding=10, font=("微軟正黑體", 9))
            style.map("Danger.TButton", background=[('active', '#c82333')])
            style.configure("Info.TButton", background="#17a2b8", foreground="white",
                            borderwidth=0, padding=10, font=("微軟正黑體", 9))
            style.map("Info.TButton", background=[('active', '#138496')])
            style.configure("Modern.Horizontal.TProgressbar", background=colors["success"],
                            troughcolor=colors["border"], borderwidth=0, lightcolor=colors["success"],
                            darkcolor=colors["success"])

        def _setup_ui(self):
            colors = self.COLORS[self.theme]
            main = tk.Frame(self.root, bg=colors["bg"])
            main.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)
            self._create_header(main, colors)
            self._create_folder_selector(main, colors)
            self._create_progress_section(main, colors)
            self._create_button_section(main, colors)
            self._create_log_section(main, colors)
            self._log("系統初始化成功")
            self._log(f"TAF 檢測系統 v1.2 已就緒（混合 DPI: {Config.OCR_DPI_LOW} → {Config.OCR_DPI_HIGH}，Batch: {Config.OCR_BATCH_SIZE}）")
            self._log(f"OCR 引擎: EasyOCR ({'GPU' if torch.cuda.is_available() else 'CPU'} 模式)")

        def _create_header(self, parent, colors):
            header = tk.Frame(parent, bg=colors["bg"])
            header.pack(fill=tk.X, pady=(0, 25))
            title_frame = tk.Frame(header, bg=colors["bg"])
            title_frame.pack(side=tk.LEFT)
            tk.Label(title_frame, text="TAF 標章自動檢測", font=("微軟正黑體", 24, "bold"),
                     fg=colors["fg"], bg=colors["bg"]).pack(anchor=tk.W)
            tk.Label(title_frame, text="v1.2 Crash Safe", font=("微軟正黑體", 10),
                     fg=colors["primary"], bg=colors["bg"]).pack(anchor=tk.W)
            right_frame = tk.Frame(header, bg=colors["bg"])
            right_frame.pack(side=tk.RIGHT)
            self.status_label = tk.Label(right_frame, text="", font=("Consolas", 9),
                                         fg=colors["success"], bg=colors["bg"])
            self.status_label.pack(side=tk.RIGHT, padx=(15, 0))
            ttk.Button(right_frame, text="⚙ 設定", command=self._open_config,
                       style="Success.TButton").pack(side=tk.RIGHT)
            self._update_status()

        def _create_folder_selector(self, parent, colors):
            folder_card = ttk.LabelFrame(parent, text=" 資料夾路徑 ", padding=15)
            folder_card.pack(fill=tk.X, pady=(0, 20))
            folder_inner = tk.Frame(folder_card, bg=colors["surface"])
            folder_inner.pack(fill=tk.X, padx=0, pady=0)
            self.folder_var = tk.StringVar()
            folder_entry = tk.Entry(folder_inner, textvariable=self.folder_var,
                                    font=("Consolas", 10), bg=colors["bg"], fg=colors["fg"],
                                    relief=tk.FLAT, bd=0, highlightthickness=2,
                                    highlightbackground=colors["border"],
                                    highlightcolor=colors["primary"])
            folder_entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, ipady=12, padx=(0, 10))
            browse_btn = ttk.Button(folder_inner, text="📁 瀏覽", command=self._select_folder,
                                    style="Info.TButton", width=12)
            browse_btn.pack(side=tk.RIGHT, ipady=2)

        def _create_progress_section(self, parent, colors):
            progress_card = ttk.Frame(parent)
            progress_card.pack(fill=tk.X, pady=(0, 20))
            progress_inner = tk.Frame(progress_card, bg=colors["surface"])
            progress_inner.pack(fill=tk.X, padx=0, pady=0)
            self.progress_var = tk.DoubleVar()
            self.progress_bar = ttk.Progressbar(progress_inner, variable=self.progress_var,
                                                maximum=100, style="Modern.Horizontal.TProgressbar",
                                                length=400)
            self.progress_bar.pack(fill=tk.X, pady=(10, 10), padx=10)
            self.progress_label = tk.Label(progress_inner, text="就緒",
                                           font=("Consolas", 10), fg=colors["fg"],
                                           bg=colors["surface"])
            self.progress_label.pack(pady=(0, 10))

        def _create_button_section(self, parent, colors):
            btn_frame = tk.Frame(parent, bg=colors["bg"])
            btn_frame.pack(pady=20)
            self.run_btn = ttk.Button(btn_frame, text="▶ 開始檢測",
                                      command=self._start_processing,
                                      style="Primary.TButton", width=20)
            self.run_btn.pack(side=tk.LEFT, padx=10)
            self.stop_btn = ttk.Button(btn_frame, text="⏹ 停止檢測",
                                       command=self._stop_processing,
                                       style="Danger.TButton", state="disabled", width=15)
            self.stop_btn.pack(side=tk.LEFT, padx=10)

        def _create_log_section(self, parent, colors):
            log_card = ttk.LabelFrame(parent, text=" 執行日誌 ")
            log_card.pack(fill=tk.BOTH, expand=True)
            log_inner = tk.Frame(log_card, bg=colors["surface"])
            log_inner.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
            log_frame = tk.Frame(log_inner, bg=colors["log_bg"])
            log_frame.pack(fill=tk.BOTH, expand=True)
            self.log_text = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 9),
                                    bg=colors["log_bg"], fg=colors["log_fg"],
                                    relief=tk.FLAT, bd=0, padx=10, pady=10,
                                    highlightthickness=0, insertbackground="white")
            scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
            self.log_text.configure(yscrollcommand=scroll.set)
            self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _update_status(self):
            status_parts = [
                f"TAF: {len(self.taf_numbers)} 種",
                f"執行緒: {self.max_workers}",
                f"模式: {self.scan_mode}",
                f"OCR: {'啟用' if HAS_EASYOCR else '禁用'} (DPI {Config.OCR_DPI_LOW}->{Config.OCR_DPI_HIGH}, Batch {Config.OCR_BATCH_SIZE})"
            ]
            status_text = " | ".join(status_parts)
            self.status_label.config(text=status_text)

        def _open_config(self):
            colors = self.COLORS[self.theme]
            win = Toplevel(self.root)
            win.title("設定 - TAF 標章檢測系統")
            win.geometry("750x750")
            win.resizable(False, False)
            win.configure(bg=colors["bg"])
            win.transient(self.root)
            win.grab_set()
            x = (win.winfo_screenwidth() - 750) // 2
            y = (win.winfo_screenheight() - 750) // 2
            win.geometry(f"750x750+{x}+{y}")
            container = tk.Frame(win, bg=colors["bg"])
            container.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)
            tk.Label(container, text="TAF 標章設定",
                     font=("微軟正黑體", 16, "bold"), fg=colors["fg"],
                     bg=colors["bg"]).pack(pady=(0, 15))
            self._create_logo_list_section(container, colors)
            self._create_settings_section(container, colors, win)
            button_frame = tk.Frame(container, bg=colors["bg"])
            button_frame.pack(pady=(20, 0))
            ttk.Button(button_frame, text="💾 儲存設定",
                       command=lambda: self._save_config(win),
                       style="Primary.TButton").pack(side=tk.LEFT, padx=10)
            ttk.Button(button_frame, text="取消",
                       command=win.destroy,
                       style="Danger.TButton").pack(side=tk.LEFT, padx=10)

        def _create_logo_list_section(self, parent, colors):
            list_card = tk.Frame(parent, bg=colors["surface"])
            list_card.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
            list_inner = tk.Frame(list_card, bg=colors["surface"])
            list_inner.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
            tk.Label(list_inner, text="已設定的 TAF 編號",
                     font=("微軟正黑體", 11, "bold"), fg=colors["fg"],
                     bg=colors["surface"]).pack(anchor=tk.W, pady=(0, 10))
            list_frame = tk.Frame(list_inner, bg=colors["surface"])
            list_frame.pack(fill=tk.BOTH, expand=True)
            self.config_listbox = tk.Listbox(list_frame, font=("Consolas", 10),
                                             selectmode=tk.SINGLE, bg=colors["bg"],
                                             fg=colors["fg"], relief=tk.FLAT, bd=0,
                                             highlightthickness=0, height=10)
            scroll = ttk.Scrollbar(list_frame, command=self.config_listbox.yview)
            self.config_listbox.configure(yscrollcommand=scroll.set)
            self.config_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self._update_config_listbox()
            btn_frame = tk.Frame(list_inner, bg=colors["surface"])
            btn_frame.pack(pady=(10, 0))
            ttk.Button(btn_frame, text="➕ 新增",
                       command=lambda: self._edit_logo_dialog(),
                       style="Success.TButton").pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="✏ 編輯",
                       command=lambda: self._edit_logo_dialog(self._get_selected_logo()),
                       style="Info.TButton").pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="🗑 刪除",
                       command=self._delete_logo,
                       style="Danger.TButton").pack(side=tk.LEFT, padx=5)

        def _create_settings_section(self, parent, colors, win):
            settings_card = tk.Frame(parent, bg=colors["surface"])
            settings_card.pack(fill=tk.X)
            settings_inner = tk.Frame(settings_card, bg=colors["surface"])
            settings_inner.pack(fill=tk.X, padx=20, pady=20)
            settings_inner.columnconfigure(0, weight=1)
            settings_inner.columnconfigure(1, weight=1)
            left_frame = tk.Frame(settings_inner, bg=colors["surface"])
            left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
            tk.Label(left_frame, text="執行緒數量",
                     font=("微軟正黑體", 10, "bold"), fg=colors["fg"],
                     bg=colors["surface"]).pack(anchor=tk.W, pady=(0, 5))
            worker_frame = tk.Frame(left_frame, bg=colors["surface"])
            worker_frame.pack(fill=tk.X, pady=(0, 15))
            self.worker_var = tk.IntVar(value=self.max_workers)
            worker_spin = tk.Spinbox(worker_frame, from_=1, to=32,
                                     textvariable=self.worker_var, width=10,
                                     font=("Segoe UI", 10), bg=colors["bg"],
                                     fg=colors["fg"], relief=tk.SOLID, bd=1)
            worker_spin.pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(worker_frame, text="(建議: 2-6，記憶體安全)",
                     font=("微軟正黑體", 9), fg=colors["fg"],
                     bg=colors["surface"]).pack(side=tk.LEFT)
            tk.Label(left_frame, text="掃描模式",
                     font=("微軟正黑體", 10, "bold"), fg=colors["fg"],
                     bg=colors["surface"]).pack(anchor=tk.W, pady=(0, 5))
            self.scan_mode_var = tk.StringVar(value=self.scan_mode)
            radio_frame = tk.Frame(left_frame, bg=colors["surface"])
            radio_frame.pack(fill=tk.X)
            tk.Radiobutton(radio_frame, text="掃到為止（快速）",
                           variable=self.scan_mode_var, value="scan_to_first",
                           bg=colors["surface"], fg=colors["fg"],
                           selectcolor=colors["bg"],
                           activebackground=colors["surface"],
                           activeforeground=colors["fg"],
                           font=("微軟正黑體", 9)).pack(anchor=tk.W)
            tk.Radiobutton(radio_frame, text="全掃描（完整掃描所有頁面）",
                           variable=self.scan_mode_var, value="full_scan",
                           bg=colors["surface"], fg=colors["fg"],
                           selectcolor=colors["bg"],
                           activebackground=colors["surface"],
                           activeforeground=colors["fg"],
                           font=("微軟正黑體", 9)).pack(anchor=tk.W)
            right_frame = tk.Frame(settings_inner, bg=colors["surface"])
            right_frame.grid(row=0, column=1, sticky="nsew")
            tk.Label(right_frame, text="OCR 參數設定", font=("微軟正黑體", 11, "bold"),
                     fg=colors["fg"], bg=colors["surface"]).pack(anchor=tk.W, pady=(0, 5))
            tk.Label(right_frame, text="Low DPI (首次掃描):", font=("微軟正黑體", 10),
                     fg=colors["fg"], bg=colors["surface"]).pack(anchor=tk.W)
            self.low_dpi_var = tk.IntVar(value=self.ocr_dpi_low)
            ttk.Spinbox(right_frame, from_=100, to=300, increment=25, textvariable=self.low_dpi_var, width=10).pack(anchor=tk.W, pady=5)
            tk.Label(right_frame, text="High DPI (重試):", font=("微軟正黑體", 10),
                     fg=colors["fg"], bg=colors["surface"]).pack(anchor=tk.W)
            self.high_dpi_var = tk.IntVar(value=self.ocr_dpi_high)
            ttk.Spinbox(right_frame, from_=150, to=400, increment=25, textvariable=self.high_dpi_var, width=10).pack(anchor=tk.W, pady=5)
            tk.Label(right_frame, text="EasyOCR Batch Size (建議1-4):", font=("微軟正黑體", 10),
                     fg=colors["fg"], bg=colors["surface"]).pack(anchor=tk.W)
            self.batch_size_var = tk.IntVar(value=self.ocr_batch_size)
            ttk.Spinbox(right_frame, from_=1, to=8, increment=1, textvariable=self.batch_size_var, width=10).pack(anchor=tk.W, pady=5)

        def _get_selected_logo(self) -> Optional[Tuple[str, dict]]:
            sel = self.config_listbox.curselection()
            if not sel:
                return None
            text = self.config_listbox.get(sel[0])
            num = text.split(" → ")[0]
            if num in self.taf_numbers:
                return (num, self.taf_numbers[num])
            return None

        def _edit_logo_dialog(self, config_tuple: Optional[Tuple[str, dict]] = None):
            colors = self.COLORS[self.theme]
            dialog = Toplevel(self.root)
            dialog.title("新增/編輯 TAF 編號")
            dialog.geometry("450x400")
            dialog.configure(bg=colors["bg"])
            dialog.transient(self.root)
            dialog.grab_set()
            is_new = config_tuple is None
            num = config_tuple[0] if config_tuple else ""
            data = config_tuple[1] if config_tuple else {"domain": "", "keywords": []}
            container = tk.Frame(dialog, bg=colors["bg"])
            container.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)
            tk.Label(container, text="TAF 編號（必填）:",
                     font=("微軟正黑體", 10, "bold"), fg=colors["fg"],
                     bg=colors["bg"]).pack(anchor=tk.W, pady=(0, 5))
            num_var = tk.StringVar(value=num)
            num_entry = tk.Entry(container, textvariable=num_var,
                                 font=("Consolas", 11), bg=colors["surface"],
                                 fg=colors["fg"], relief=tk.SOLID, bd=1)
            num_entry.pack(fill=tk.X, ipady=8, pady=(0, 15))
            if not is_new:
                num_entry.config(state="disabled")
            tk.Label(container, text="領域別（必填，Y/Z/I/X/R/W）:",
                     font=("微軟正黑體", 10, "bold"), fg=colors["fg"],
                     bg=colors["bg"]).pack(anchor=tk.W, pady=(0, 5))
            domain_var = tk.StringVar(value=data["domain"])
            domain_entry = tk.Entry(container, textvariable=domain_var,
                                    font=("Consolas", 11), bg=colors["surface"],
                                    fg=colors["fg"], relief=tk.SOLID, bd=1, width=10)
            domain_entry.pack(ipady=8, pady=(0, 15))
            tk.Label(container, text="驗證關鍵字（用逗號分隔）:",
                     font=("微軟正黑體", 10, "bold"), fg=colors["fg"],
                     bg=colors["bg"]).pack(anchor=tk.W, pady=(0, 5))
            tk.Label(container, text="例: civilengineeringlaboratory,civil,engineering",
                     font=("Consolas", 8), fg="#6c757d", bg=colors["bg"]).pack(anchor=tk.W, padx=30, pady=(0, 5))
            keywords_var = tk.StringVar(value=",".join(data.get("keywords", [])))
            keywords_entry = tk.Entry(container, textvariable=keywords_var,
                                      font=("Consolas", 10), bg=colors["surface"],
                                      fg=colors["fg"], relief=tk.SOLID, bd=1)
            keywords_entry.pack(fill=tk.X, ipady=8, pady=(0, 20))

            def save():
                n = num_var.get().strip()
                d = domain_var.get().strip().upper()
                k = keywords_var.get().strip()
                if not n or not d or not k:
                    messagebox.showerror("錯誤", "所有欄位都必填", parent=dialog)
                    return
                keywords_list = [kw.strip().lower() for kw in k.split(",") if kw.strip()]
                if not keywords_list:
                    messagebox.showerror("錯誤", "至少需要一個關鍵字", parent=dialog)
                    return
                self.taf_numbers[n] = {"domain": d, "keywords": keywords_list}
                self._update_config_listbox()
                self._update_status()
                dialog.destroy()

            ttk.Button(container, text="💾 儲存", command=save,
                       style="Primary.TButton").pack()

        def _delete_logo(self):
            config_tuple = self._get_selected_logo()
            if not config_tuple:
                messagebox.showwarning("提示", "請選擇一個編號")
                return
            num = config_tuple[0]
            if messagebox.askyesno("確認刪除", f"確定刪除 TAF 編號 {num}？", parent=self.root):
                del self.taf_numbers[num]
                self._update_config_listbox()
                self._update_status()

        def _update_config_listbox(self):
            self.config_listbox.delete(0, tk.END)
            for num, data in sorted(self.taf_numbers.items()):
                domain = data.get("domain", "X")
                keywords = data.get("keywords", [])
                kw_display = ", ".join(keywords[:2])
                if len(keywords) > 2:
                    kw_display += "..."
                display_text = f"{num} → {domain} | {kw_display}"
                self.config_listbox.insert(tk.END, display_text)

        def _save_config(self, win):
            try:
                self.max_workers = self.worker_var.get()
                self.scan_mode = self.scan_mode_var.get()
                self.ocr_dpi_low = self.low_dpi_var.get()
                self.ocr_dpi_high = self.high_dpi_var.get()
                self.ocr_batch_size = self.batch_size_var.get()
                Config.OCR_DPI_LOW = self.ocr_dpi_low
                Config.OCR_DPI_HIGH = self.ocr_dpi_high
                Config.OCR_BATCH_SIZE = self.ocr_batch_size

                self.config.update({
                    "taf_numbers": self.taf_numbers,
                    "max_workers": self.max_workers,
                    "scan_mode": self.scan_mode,
                    "theme": self.theme,
                    "ocr_dpi_low": self.ocr_dpi_low,
                    "ocr_dpi_high": self.ocr_dpi_high,
                    "ocr_batch_size": self.ocr_batch_size
                })
                if ConfigManager.save_config(self.config):
                    self._update_status()
                    self._log("✓ 設定已儲存（含 OCR 參數）")
                    win.destroy()
                else:
                    messagebox.showerror("錯誤", "儲存失敗", parent=win)
            except ValueError:
                messagebox.showerror("錯誤", "請輸入有效的數字", parent=win)

            # _show_splash 邏輯已移至 startup
            pass

        def _on_closing(self):
            if self.memory_monitor_id:
                try:
                    self.root.after_cancel(self.memory_monitor_id)
                except:
                    pass
            self.anti_sleep.stop()
            try:
                self.root.quit()
                self.root.update_idletasks()
                self.root.update()
            except:
                pass
            dialog = Toplevel(self.root)
            dialog.title("確認關閉")
            dialog.geometry("350x150")
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.configure(bg=self.COLORS[self.theme]["bg"])
            x = (dialog.winfo_screenwidth() - 350) // 2
            y = (dialog.winfo_screenheight() - 150) // 2
            dialog.geometry(f"350x150+{x}+{y}")
            message_label = tk.Label(dialog, text="確定要關閉應用程式嗎？",
                                     font=("微軟正黑體", 10),
                                     bg=self.COLORS[self.theme]["bg"],
                                     fg=self.COLORS[self.theme]["fg"])
            message_label.pack(pady=20)
            button_frame = tk.Frame(dialog, bg=self.COLORS[self.theme]["bg"])
            button_frame.pack(pady=10)
            confirm_btn = ttk.Button(button_frame, text="確認退出",
                                     command=lambda: [dialog.destroy(), self._quit_app()],
                                     style="Danger.TButton")
            confirm_btn.pack(side=tk.LEFT, padx=5)
            cancel_btn = ttk.Button(button_frame, text="取消",
                                    command=dialog.destroy,
                                    style="Info.TButton")
            cancel_btn.pack(side=tk.LEFT, padx=5)
            dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
            dialog.wait_window()
            self.root.destroy()

        def _quit_app(self):
            self._log("正在關閉應用程式...")
            if self.stop_event:
                self.stop_event.set()
            if self.executor:
                try:
                    self.executor.shutdown(wait=False, cancel_futures=True)
                except:
                    pass
            self.anti_sleep.stop()
            try:
                current_pid = os.getpid()
                parent = psutil.Process(current_pid)
                for child in parent.children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                gone, alive = psutil.wait_procs(parent.children(recursive=True), timeout=3)
                for p in alive:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
            except Exception as e:
                print(f"[WARNING] 清理進程時發生錯誤: {e}")
            gc.collect()

        def _select_folder(self):
            path = filedialog.askdirectory(title="請選擇包含 PDF 檔案的資料夾")
            if path:
                self.folder_path = path
                self.folder_var.set(path)
                self._log(f"📁 已選擇：{os.path.basename(path)}")

        def _start_processing(self):
            if not self.folder_path:
                messagebox.showerror("錯誤", "請先選擇資料夾！")
                return
            mapping_file = os.path.join(self.folder_path, Config.DOMAIN_MAPPING_FILE)
            if not os.path.exists(mapping_file):
                messagebox.showerror("錯誤", f"資料夾中找不到 {Config.DOMAIN_MAPPING_FILE}！")
                return
            self.stop_event = threading.Event()
            self.run_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self._reset_progress()
            self._update_status()
            self.anti_sleep.start()
            self._log("=" * 70)
            self._log("▶ 開始檢測...")
            self._log(f" 模式: {self.scan_mode}")
            self._log(f" 執行緒: {self.max_workers}")
            self._log(f" 策略: EasyOCR 混合 DPI ({Config.OCR_DPI_LOW} → {Config.OCR_DPI_HIGH}) Batch: {Config.OCR_BATCH_SIZE}")
            threading.Thread(target=self._run_detection, daemon=True).start()

        def _stop_processing(self):
            self._log("⚠ 正在立即停止檢測...")
            if self.stop_event:
                self.stop_event.set()
            self.anti_sleep.stop()
            if self.executor:
                try:
                    self.executor._threads.clear()
                    self.executor.shutdown(wait=False, cancel_futures=True)
                except:
                    pass
            self.stop_btn.config(state="disabled")
            self._log("✓ 停止訊號已發送")

        def _run_detection(self):
            self.root.after(0, self._update_status)
            try:
                self._log("正在讀取檔案清單...")
                self.root.update_idletasks()
                all_files = os.listdir(self.folder_path)
                pdf_files = [f for f in all_files if f.lower().endswith('.pdf')]
                self.total_files = len(pdf_files)
                self._log(f"找到 {self.total_files} 個 PDF 檔案")
                self.root.update_idletasks()
                if not self.total_files:
                    self._show_result("提示", "資料夾中沒有 PDF 檔案")
                    self._finalize()
                    return
                mem = psutil.virtual_memory()
                if mem.percent >= 90:
                    self._log(f"[MEM WARNING] 記憶體使用 {mem.percent:.1f}%，暫停 5 秒...")
                    time.sleep(5)
                    release_system_memory()
                domain_map = self._load_domain_mapping(
                    os.path.join(self.folder_path, Config.DOMAIN_MAPPING_FILE)
                )
                if not domain_map:
                    self._finalize()
                    return
                global all_results
                all_results = []
                report_to_files = {}
                processed = set()
                completed_count = 0

                # 1. 掃描檔案並建立初步清單
                for f in pdf_files:
                    path = os.path.join(self.folder_path, f)
                    name = os.path.splitext(f)[0]
                    nums = re.findall(r"([A-Z][A-Z0-9]-\d{2}-\d{5,6}(?:-\d{1,2})?[A-Z]?(?!\d))", name)
                    
                    if not nums:
                        result = {
                            "報告編號": "N/A",
                            "檔案名稱": f,
                            "應有領域別": "N/A",
                            "檢測PDF領域別": "N/A",
                            "掃描Logo編號": "N/A",
                            "判別結果": "跳過",
                            "錯誤說明": "檔名格式錯誤"
                        }
                        all_results.append(result)
                        processed.add(f)
                        completed_count += 1
                        self._update_progress(completed_count, self.total_files, result)
                        continue
                    
                    for n in nums:
                        report_to_files.setdefault(n, []).append((path, f))

                tasks = []
                
                # 2. 根據對照表分配任務
                for num, expected in domain_map.items():
                    if num not in report_to_files:
                        continue
                    
                    for path, f in report_to_files[num]:
                        if f in processed:
                            continue
                        
                        processed.add(f)
                        
                        if expected == "W":
                            result = {
                                "報告編號": num,
                                "檔案名稱": f,
                                "應有領域別": "W",
                                "檢測PDF領域別": "X",
                                "掃描Logo編號": "未掃描",
                                "判別結果": "跳過",
                                "錯誤說明": "對照表為 W"
                            }
                            all_results.append(result)
                            completed_count += 1
                            self._update_progress(completed_count, self.total_files, result)
                            continue
                        
                        # 預先加入待檢測項目到清單
                        pending_result = {
                            "報告編號": num,
                            "檔案名稱": f,
                            "應有領域別": expected,
                            "檢測PDF領域別": "待檢測",
                            "掃描Logo編號": "",
                            "判別結果": "待檢測",
                            "錯誤說明": ""
                        }
                        all_results.append(pending_result)
                        
                        tasks.append((path, f, num, expected, self.taf_numbers,
                                      self.scan_mode, self.stop_event))

                # 3. 處理不在對照表中的檔案
                for f in pdf_files:
                    if f in processed:
                        continue
                    
                    name = os.path.splitext(f)[0]
                    nums = re.findall(r"([A-Z][A-Z0-9]-\d{2}-\d{5,6}[A-Z]?(?!\d))", name)
                    display = nums[-1] if nums else "N/A"
                    
                    result = {
                        "報告編號": display,
                        "檔案名稱": f,
                        "應有領域別": "N/A",
                        "檢測PDF領域別": "N/A",
                        "掃描Logo編號": "未掃描",
                        "判別結果": "跳過",
                        "錯誤說明": "不在對照表"
                    }
                    all_results.append(result)
                    processed.add(f)
                    completed_count += 1
                    self._update_progress(completed_count, self.total_files, result)

                # 初始自動儲存（包含所有待檢測項目）
                try:
                    autosave_path = os.path.join(Config.get_app_dir(), Config.AUTOSAVE_FILE)
                    ExcelReporter.export_report(all_results, autosave_path)
                    self._log(f"[AUTO-SAVE] 初始清單已建立並儲存至 {autosave_path}")
                except Exception as e:
                    self._log(f"[WARNING] 初始存檔失敗: {e}")

                if tasks:
                    self._log(f"開始處理 {len(tasks)} 個掃描任務...")
                    self.root.update_idletasks()
                    
                    batch_size = Config.OCR_BATCH_SIZE
                    for i in range(0, len(tasks), batch_size):
                        if self.stop_event.is_set():
                            remaining = len(tasks) - i
                            self._log(f"停止訊號觸發，中斷剩餘 {remaining} 個任務")
                            break
                            
                        batch_tasks = tasks[i:i + batch_size]
                        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
                        
                        try:
                            futures = {
                                self.executor.submit(PDFProcessor.process_pdf, t): t[1]
                                for t in batch_tasks
                            }
                            
                            for future in as_completed(futures):
                                if self.stop_event.is_set():
                                    self._log("停止訊號觸發，取消剩餘任務")
                                    for f in futures:
                                        if not f.done():
                                            f.cancel()
                                    break
                                
                                r = future.result()
                                if r:
                                    # 更新現有結果而不是附加
                                    found_idx = -1
                                    for idx, item in enumerate(all_results):
                                        if item["檔案名稱"] == r["檔案名稱"]:
                                            found_idx = idx
                                            break
                                    
                                    if found_idx != -1:
                                        all_results[found_idx] = r
                                    else:
                                        # 理論上不應發生，除非檔名重複或邏輯錯誤
                                        all_results.append(r)
                                    
                                    completed_count += 1
                                    self._update_progress(completed_count, self.total_files, r)
                                    
                                    if completed_count % 10 == 0:
                                        try:
                                            ExcelReporter.export_report(all_results, autosave_path)
                                            self._log(f"[AUTO-SAVE] 已儲存進度至 {autosave_path} ({completed_count}/{self.total_files})")
                                        except Exception as e:
                                            print(f"[WARNING] 自動存檔失敗: {e}")

                            release_system_memory()
                            gc.collect()
                            time.sleep(2)
                            mem = psutil.Process().memory_info().rss / (1024 ** 2)
                            print(f"[MEM BATCH END] 批次 {i//batch_size + 1} 完成 | 記憶體使用: {mem:.1f} MB")
                            
                        finally:
                            self.executor.shutdown(wait=False)
                            self.executor = None
                            gc.collect()

                if self.stop_event.is_set():
                    self._log("檢測已手動停止")
                    self._show_result("已停止", "檢測已手動停止。\n已處理的結果仍會匯出。")

                success = sum(1 for r in all_results if r["判別結果"] == "正確")
                error = sum(1 for r in all_results if r["判別結果"] == "錯誤")
                skip = self.total_files - success - error
                
                output = os.path.join(Config.get_app_dir(), Config.OUTPUT_REPORT_FILE)
                if ExcelReporter.export_report(all_results, output):
                    self._log("=" * 70)
                    self._log(f"完成！正確: {success}, 錯誤: {error}, 跳過: {skip}")
                    if not self.stop_event.is_set():
                        self._show_result(
                            "完成",
                            f"正確：{success}\n"
                            f"錯誤：{error}\n"
                            f"跳過：{skip}\n\n"
                            f"報告：{output}"
                        )
                else:
                    self._show_result("錯誤", "報告匯出失敗")
            except Exception as e:
                self._log(f"發生錯誤: {e}")
                self._show_result("錯誤", f"程式錯誤：{e}")
            finally:
                self._finalize()

        def _finalize(self):
            gc.collect()
            self.anti_sleep.stop()
            if self.executor:
                try:
                    self.executor.shutdown(wait=False, cancel_futures=True)
                except:
                    pass
            self.executor = None
            self.stop_event = None
            self.root.after(0, lambda: [
                self.run_btn.config(state="normal"),
                self.stop_btn.config(state="disabled"),
                self._reset_progress()
            ])

        def _get_log_message(self, result: Dict) -> str:
            status = result['判別結果']
            file = result['檔案名稱']
            if status == '正確':
                return f"✓ 正確 - {file}"
            elif status == '跳過':
                return f"⊘ 跳過 - {file} ({result['錯誤說明']})"
            elif status == '錯誤':
                return f"✗ 錯誤 - {file} ({result['錯誤說明']})"
            else:
                return f"? 未知 - {file}"

        def _update_progress(self, current: int, total: int, result: Dict):
            percentage = (current / total) * 100
            log_msg = self._get_log_message(result)
            self.root.after(0, lambda: [
                self.progress_var.set(percentage),
                self.progress_label.config(
                    text=f"處理中 | {current}/{total} ({percentage:.1f}%)"
                ),
                self._log(log_msg),
                self.log_text.see(tk.END),
                self.root.update_idletasks()
            ])

        def _reset_progress(self):
            self.progress_var.set(0)
            self.progress_label.config(text="就緒")

        def _show_result(self, title: str, message: str):
            self.root.after(0, lambda: (
                messagebox.showinfo(title, message)
                if "完成" in title or "停止" in title
                else messagebox.showerror(title, message)
            ))

        def _log(self, message: str):
            if self.root.winfo_exists():
                timestamp = time.strftime('%H:%M:%S')
                self.root.after(0, lambda: [
                    self.log_text.insert(tk.END, f"[{timestamp}] {message}\n"),
                    self.log_text.see(tk.END),
                    self.root.update_idletasks()
                ])

        def _load_domain_mapping(self, path: str) -> Optional[Dict]:
            try:
                df = pd.read_excel(
                    path,
                    usecols=['報告編號', '領域別'],
                    dtype=str
                )
                mapping = {
                    str(row['報告編號']).strip(): str(row['領域別']).strip()
                    for _, row in df.iterrows()
                    if pd.notna(row['報告編號']) and pd.notna(row['領域別'])
                }
                self._log(f"✓ 載入對照表：{len(mapping)} 筆記錄")
                return mapping
            except FileNotFoundError:
                self._log(f"✗ 找不到對照表：{os.path.basename(path)}")
                self._show_result(
                    "錯誤",
                    f"在選定的資料夾中找不到 '{os.path.basename(path)}'。\n"
                    f"請確認檔名是否為 DomainMapping.xlsx"
                )
                return None
            except Exception as e:
                self._log(f"✗ 讀取對照表失敗: {e}")
                self._show_result("錯誤", f"讀取 DomainMapping.xlsx 失敗：\n{e}")
                return None

    root = tk.Tk()
    app = TAFCheckerApp(root)
    root.mainloop()

# ============================================================================
# 程式入口點
# ============================================================================
if __name__ == "__main__":
    try:
        print("=" * 70)
        print("TAF Logo Auto-Detection System v1.2 - Enhanced Crash Protection")
        print("=================================================================")
        print("針對 Ryzen 5 PRO 4650U + 16GB RAM 優化")
        print("- 混合 DPI 策略: 可設定 Low/High DPI")
        print("- EasyOCR batch_size 可設定")
        print("- 每10檔案自動儲存進度")
        print("- 記憶體監控: >11GB 降執行緒, >14GB 暫停30s")
        print("- 批次結束強制清空 CUDA 快取")
        print("- 全域崩潰捕捉 + 自動儲存恢復點")
        print("作者: TAF System Team (強化版)")
        print("版本: 1.2 - Crash Safe + Configurable OCR + Fixed Save Paths")
        print("日期: 2025")
        print("Python: 3.8+")
        print("=" * 70)
        print()
        # main() 改為由 startup 調用
        startup()
    except KeyboardInterrupt:
        print("\n[INFO] 應用程式被使用者中斷")
    except Exception as e:
        print(f"\n[FATAL ERROR] 嚴重錯誤: {e}")
        import traceback
        print("\n完整錯誤追蹤：")
        print("-" * 70)
        traceback.print_exc()
        print("-" * 70)
    finally:
        print("\n[INFO] 應用程式關閉完成")
        print("=" * 70)
        gc.collect()