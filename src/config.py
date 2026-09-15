import os
import re
from pathlib import Path

class Config:
    def __init__(self, root_dir: Path | None = None):
        # プロジェクトルートの決定
        if root_dir is None:
            # このファイル（src/config.py）の2階層上をルートとする
            self.PROJECT_ROOT = Path(__file__).resolve().parent.parent
        else:
            self.PROJECT_ROOT = Path(root_dir)

        # デフォルト値の設定
        self.PORT = 8080
        self.GEMINI_API_KEY = None
        self.ENV = "local"
        self.DATA_DIR = self.PROJECT_ROOT / "data"

        # .env のロードと優先度適用
        self._load_and_apply()

    def _load_and_apply(self):
        env_path = self.PROJECT_ROOT / ".env"
        env_vars = {}
        if env_path.exists():
            try:
                # UTF-8 で日本語コメントに対応
                content = env_path.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    # 空行やコメント行の無視
                    if not line or line.startswith("#"):
                        continue
                    # KEY=VALUE のパース
                    match = re.match(r"^([^=]+)=(.*)$", line)
                    if match:
                        key = match.group(1).strip()
                        val = match.group(2).strip()
                        # ダブルクォーテーションやシングルクォーテーションの除去
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        # 値の後ろにコメントがある場合の対応（例：PORT=8080 # port）
                        if " #" in val:
                            val = val.split(" #", 1)[0].strip()
                        elif "\t#" in val:
                            val = val.split("\t#", 1)[0].strip()
                        env_vars[key] = val
            except Exception:
                # 読み込み失敗時は無視（デフォルトフォールバック）
                pass

        # 環境変数の適用優先順位: OS環境変数 > .env > デフォルト値

        # 1. ENV
        env_val = os.environ.get("ENV") or env_vars.get("ENV")
        if env_val:
            self.ENV = env_val

        # 2. PORT
        port_val = os.environ.get("PORT") or env_vars.get("PORT")
        if port_val is not None:
            try:
                self.PORT = int(port_val)
            except ValueError:
                # 不正な値（"abc"など）の場合はデフォルトに安全にフォールバック
                self.PORT = 8080

        # 3. GEMINI_API_KEY
        key_val = os.environ.get("GEMINI_API_KEY") or env_vars.get("GEMINI_API_KEY")
        if key_val:
            self.GEMINI_API_KEY = key_val

        # 4. DATA_DIR
        data_dir_val = os.environ.get("DATA_DIR") or env_vars.get("DATA_DIR")
        if data_dir_val:
            self.DATA_DIR = Path(data_dir_val)
        else:
            self.DATA_DIR = self.PROJECT_ROOT / "data"

# グローバルインスタンス
config = Config()
