#!/usr/bin/env python3
"""
Demonstration of the new configurable HTML index and fallback feature in StaticFiles.

This shows how to use custom index and 404 fallback filenames for SPA and documentation
site deployments.
"""

import tempfile
from pathlib import Path
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient


def demo_default_html_mode():
    """Traditional html=True with default index.html and 404.html"""
    print("\n" + "="*70)
    print("DEMO 1: Default HTML Mode (backward compatible)")
    print("="*70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create structure
        (tmpdir / "index.html").write_text("<h1>Default Home</h1>")
        (tmpdir / "404.html").write_text("<h1>Default 404</h1>")
        (tmpdir / "docs").mkdir()
        (tmpdir / "docs" / "index.html").write_text("<h1>Docs Home</h1>")

        app = StaticFiles(directory=tmpdir, html=True)
        client = TestClient(app)

        # Test root directory
        response = client.get("/")
        print(f"\nGET / -> {response.status_code}")
        print(f"Content: {response.text}")

        # Test subdirectory
        response = client.get("/docs/")
        print(f"\nGET /docs/ -> {response.status_code}")
        print(f"Content: {response.text}")

        # Test 404
        response = client.get("/missing")
        print(f"\nGET /missing -> {response.status_code}")
        print(f"Content: {response.text}")


def demo_spa_mode():
    """SPA deployment with custom index (app.html) and fallback (app.html)"""
    print("\n" + "="*70)
    print("DEMO 2: SPA Mode with Custom Index and Fallback")
    print("="*70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create SPA structure
        (tmpdir / "app.html").write_text("<!DOCTYPE html><html><body><div id='app'></div></body></html>")
        (tmpdir / "static").mkdir()
        (tmpdir / "static" / "app.js").write_text("console.log('SPA app');")

        # Both index and fallback point to the same SPA entry point
        app = StaticFiles(
            directory=tmpdir,
            html=True,
            html_index="app.html",
            html_fallback="app.html"
        )
        client = TestClient(app)

        # Test root - serves SPA
        response = client.get("/")
        print(f"\nGET / -> {response.status_code}")
        print(f"Content: {response.text[:60]}...")

        # Test deep link - also serves SPA (client-side routing)
        response = client.get("/users/123/profile")
        print(f"\nGET /users/123/profile -> {response.status_code}")
        print(f"Content: {response.text[:60]}...")

        # Test static asset
        response = client.get("/static/app.js")
        print(f"\nGET /static/app.js -> {response.status_code}")
        print(f"Content: {response.text}")


def demo_docs_site_mode():
    """Documentation site with custom home.html and custom 404"""
    print("\n" + "="*70)
    print("DEMO 3: Documentation Site with Custom Index and 404")
    print("="*70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create docs structure
        (tmpdir / "home.html").write_text("<h1>Welcome to Our Docs</h1>")
        (tmpdir / "not-found.html").write_text("<h1>Page Not Found - Check the URL</h1>")

        (tmpdir / "api").mkdir()
        (tmpdir / "api" / "home.html").write_text("<h1>API Reference</h1>")

        (tmpdir / "guide").mkdir()
        (tmpdir / "guide" / "home.html").write_text("<h1>User Guide</h1>")

        app = StaticFiles(
            directory=tmpdir,
            html=True,
            html_index="home.html",
            html_fallback="not-found.html"
        )
        client = TestClient(app)

        # Test root
        response = client.get("/")
        print(f"\nGET / -> {response.status_code}")
        print(f"Content: {response.text}")

        # Test API section
        response = client.get("/api/")
        print(f"\nGET /api/ -> {response.status_code}")
        print(f"Content: {response.text}")

        # Test guide section
        response = client.get("/guide/")
        print(f"\nGET /guide/ -> {response.status_code}")
        print(f"Content: {response.text}")

        # Test 404
        response = client.get("/old-page")
        print(f"\nGET /old-page -> {response.status_code}")
        print(f"Content: {response.text}")


def demo_redirect_behavior():
    """Verify trailing slash redirect still works with custom index"""
    print("\n" + "="*70)
    print("DEMO 4: Trailing Slash Redirect with Custom Index")
    print("="*70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        (tmpdir / "404.html").write_text("<h1>404</h1>")
        (tmpdir / "docs").mkdir()
        (tmpdir / "docs" / "main.html").write_text("<h1>Docs Main</h1>")

        app = StaticFiles(
            directory=tmpdir,
            html=True,
            html_index="main.html"
        )
        client = TestClient(app, follow_redirects=False)

        # Request without trailing slash - should redirect
        response = client.get("/docs")
        print(f"\nGET /docs (no trailing slash) -> {response.status_code}")
        print(f"Location: {response.headers.get('location')}")

        # Follow redirect
        client = TestClient(app, follow_redirects=True)
        response = client.get("/docs")
        print(f"\nGET /docs (with redirect) -> {response.status_code}")
        print(f"Final URL: {response.url}")
        print(f"Content: {response.text}")


if __name__ == "__main__":
    print("\n" + "#"*70)
    print("# StaticFiles Custom HTML Index and Fallback Feature Demo")
    print("#"*70)

    demo_default_html_mode()
    demo_spa_mode()
    demo_docs_site_mode()
    demo_redirect_behavior()

    print("\n" + "#"*70)
    print("# All demos completed successfully!")
    print("#"*70 + "\n")
