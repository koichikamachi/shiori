import os
from pathlib import Path
from src.config import Config

def test_config_defaults(tmp_path, monkeypatch):
    # テスト対象の環境変数をリセット
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    # configオブジェクトを一時フォルダ（.env なし）で生成
    cfg = Config(root_dir=tmp_path)

    assert cfg.PORT == 8080
    assert cfg.GEMINI_API_KEY is None
    assert cfg.ENV == "local"
    assert cfg.DATA_DIR == tmp_path / "data"
    assert isinstance(cfg.DATA_DIR, Path)

def test_config_load_dotenv(tmp_path, monkeypatch):
    # 環境変数をクリア
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    # 日本語コメントやスペース、クォーテーション等を含む一時 .env ファイルの作成
    dotenv_content = """
    # サーバー設定
    PORT=9000 # 開発用ポートです
    ENV="development"
    
    # API設定
    GEMINI_API_KEY = 'test_key_abc_123'
    DATA_DIR = ./custom_data
    """
    env_file = tmp_path / ".env"
    env_file.write_text(dotenv_content, encoding="utf-8")

    cfg = Config(root_dir=tmp_path)

    assert cfg.PORT == 9000
    assert cfg.ENV == "development"
    assert cfg.GEMINI_API_KEY == "test_key_abc_123"
    assert cfg.DATA_DIR == Path("./custom_data")

def test_config_os_env_priority(tmp_path, monkeypatch):
    # .env ファイルの作成
    dotenv_content = """
    PORT=9000
    ENV=development
    """
    env_file = tmp_path / ".env"
    env_file.write_text(dotenv_content, encoding="utf-8")

    # OS環境変数をセット（.env より優先されるはず）
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setenv("ENV", "production")

    cfg = Config(root_dir=tmp_path)

    assert cfg.PORT == 9999
    assert cfg.ENV == "production"

def test_config_invalid_port_fallback(tmp_path, monkeypatch):
    # 環境変数 PORT に不正な値をセット
    monkeypatch.setenv("PORT", "invalid_port_number")
    
    cfg = Config(root_dir=tmp_path)
    
    # 例外にならず、デフォルト値 8080 にフォールバックされること
    assert cfg.PORT == 8080

def test_config_data_dir_path_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    
    # 1. デフォルトの場合
    cfg = Config(root_dir=tmp_path)
    assert cfg.DATA_DIR == tmp_path / "data"
    
    # 2. カスタム指定の場合
    monkeypatch.setenv("DATA_DIR", "C:/custom/path/data")
    cfg2 = Config(root_dir=tmp_path)
    assert cfg2.DATA_DIR == Path("C:/custom/path/data")
    assert isinstance(cfg2.DATA_DIR, Path)
