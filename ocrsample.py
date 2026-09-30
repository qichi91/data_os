import cv2
import numpy as np
import torch
import torchvision.transforms as transforms
from paddleocr import PaddleOCR

# ==========================================
# 1. NDLOCR-Lite 認識クラス (CPU専用)
# ==========================================
class NDLOCRTextRecognizerCPU:
    def __init__(self, model_weight_path: str, char_dict_path: str):
        # 明示的にCPUを指定
        self.device = torch.device("cpu")
        
        # 辞書の読み込み
        self.chars = []
        with open(char_dict_path, "r", encoding="utf-8") as f:
            self.chars = [line.strip("\r\n") for line in f]
        
        # モデルをCPUメモリ上にロード
        self.model = torch.load(model_weight_path, map_location=self.device)
        self.model.eval()

        # 画像前処理
        self.transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5], std=[0.5])
        ])

    def preprocess_crop(self, crop_img: np.ndarray, target_height: int = 32) -> torch.Tensor:
        """切り出したBGR画像をモデル入力用にリサイズしてCPUテンソル化"""
        gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        target_width = max(int(w * (target_height / h)), 16)
        resized = cv2.resize(gray, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
        
        tensor = self.transform(resized).unsqueeze(0)  # [1, 1, H, W]
        return tensor.to(self.device)

    def decode(self, preds: torch.Tensor) -> str:
        """CTCデコード (CPU処理)"""
        pred_indices = preds.argmax(dim=-1).squeeze(0).tolist()
        res = []
        prev = -1
        blank_idx = 0  # 多くのモデルで0番目がCTCブランク
        
        for idx in pred_indices:
            if idx != prev and idx != blank_idx and idx < len(self.chars):
                res.append(self.chars[idx])
            prev = idx
        return "".join(res)

    def predict(self, crop_img: np.ndarray) -> str:
        tensor = self.preprocess_crop(crop_img)
        with torch.no_grad():
            output = self.model(tensor)
            text = self.decode(output)
        return text


# ==========================================
# 2. 検出領域の切り出し・射影変換
# ==========================================
def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left
    rect[2] = pts[np.argmax(s)]  # bottom-right

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect

def crop_box(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    rect = order_points(points)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = max(int(width_a), int(width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = max(int(height_a), int(height_b))

    dst = np.array([
        [0, 0],
        [max_width - 1, 0],
        [max_width - 1, max_height - 1],
        [0, max_height - 1]
    ], dtype="float32")

    m = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, m, (max_width, max_height))
    return warped


# ==========================================
# 3. 実行メイン処理
# ==========================================
def main():
    img_path = "document.jpg"
    image = cv2.imread(img_path)
    if image is None:
        raise FileNotFoundError(f"画像が見つかりません: {img_path}")

    # 1. PaddleOCR初期化 (use_gpu=False を明示)
    # ※ PaddleOCRのバージョンによっては device='cpu' も有効です
    detector = PaddleOCR(
        use_gpu=False,
        use_angle_cls=False,
        rec=False,
        lang="japan"
    )

    # 2. NDLOCR-Lite認識器初期化 (CPU固定)
    recognizer = NDLOCRTextRecognizerCPU(
        model_weight_path="./ndlocr_lite/recognizer.pth",
        char_dict_path="./ndlocr_lite/dict.txt"
    )

    # 3. テキスト領域検出
    det_results = detector.ocr(img_path, rec=False)[0]

    # 4. 各切り出し領域に対してNDLOCRで認識
    results = []
    for box in det_results:
        pts = np.array(box, dtype=np.float32)
        cropped = crop_box(image, pts)

        # ノイズなどの微小領域を除外
        if cropped.shape[0] < 5 or cropped.shape[1] < 5:
            continue

        text = recognizer.predict(cropped)
        results.append({
            "box": pts.tolist(),
            "text": text
        })

    # 結果確認
    for item in results:
        print(f"[{item['text']}] - Box: {item['box']}")

if __name__ == "__main__":
    main()
