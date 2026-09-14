"""Copy only reviewed source files into the separate release repository."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
FILES = ['app.py', 'library_core.py', 'platform_support.py', 'desktop_entry.py', 'ui_catalog.py',
    'ui_search.py', 'ui_settings.py', 'ui_theme.py', 'ui_drag.py', 'local_search.py', 'agent_search.py',
    'api_settings.py', 'paper_sections.py', 'paper_links.py', 'disk_catalog.py', 'folder_paths.py',
    'requirements.txt', 'requirements-build.txt', 'README.md', '.gitignore',
    'packaging/QUICK_START.md', 'packaging/build_release.py', 'packaging/stage_source.py',
    '.github/workflows/desktop-build.yml', 'tests/test_packaging.py', 'tests/test_inline_folders.py',
    'fulltext_agent.py', 'model_response.py', 'tests/test_model_response.py', 'tests/test_api_settings.py', 'tests/test_library.py', 'tests/test_research_features.py', 'tests/test_ui.py', 'markdown_ui.py', 'tests/test_fulltext_agent.py', 'tests/test_live_markdown.py', 'packaging/fetch_math_assets.py', 'packaging/check_graphics.py']


if __name__ == '__main__':
    destination = ROOT / 'release-repository'
    if not (destination / '.git').exists():
        raise SystemExit('Clone the destination repository first.')
    for relative in FILES:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copytree(ROOT / 'assets/katex', destination / 'assets/katex', dirs_exist_ok=True)
    print(f'Staged {len(FILES)} source and build files; no API configuration, database, papers or vendor folder.')
