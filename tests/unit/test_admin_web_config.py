from __future__ import annotations

from pathlib import Path


def test_admin_web_nginx_serves_the_vite_build_directory() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    config = (repository_root / "frontend" / "nginx.conf").read_text()

    assert "root /usr/share/nginx/html;" in config
    assert "index index.html;" in config
    assert "try_files $uri $uri/ /index.html;" in config
