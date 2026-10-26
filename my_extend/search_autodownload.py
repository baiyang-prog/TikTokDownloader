"""
搜索采集后自动下载作品文件（可选开关 / 运行期补丁，不改动上游任何文件）

背景
    「7. 采集搜索结果数据(抖音)」在上游只做数据采集：调用 Extractor 把字段写进
    CSV / XLSX / SQLite，全程不调用 Downloader，所以只有表格、没有图片和视频。
    本模块在运行期给 `deal_search_data` 套一层装饰，采集完成后顺带把作品文件下载下来。

用法
    1. 打开 my_extend/config.json，把 search_auto_download.enable 设为 true 即启用；
       设为 false 即完全恢复上游行为（config.json 每次调用都会重新读取，改完立即生效）。
    2. 用 my_extend/start.py 启动程序，不要用 main.py：
           .venv\\Scripts\\python.exe my_extend\\start.py
       PyCharm 里右键 my_extend/start.py → Run 也可以；start.py 内部就是复用 main.py 的入口。

配置项（my_extend/config.json 的 search_auto_download 段）
    enable        总开关，true / false
    tabs          触发下载的搜索模式，0=综合搜索 1=视频搜索（用户/直播搜索没有文件可下，不要加）
    folder_name   下载子目录名，支持 {keyword} 占位符；设为 "" 表示直接放进 Download 目录
    default_pages 只输入关键词、没写页数时使用的默认页数；0 表示沿用上游行为（1 页）
    echo_params   是否在控制台回显解析后的搜索参数和实际返回条数，true / false

说明
    - 上游 settings.json 里 download=false 时本模块自动跳过，不会绕开总下载开关；
    - 作品会按 ID 去重，已下载过的文件由上游的下载记录机制跳过；
    - 上游把 `deal_search_data` 改名或挪位置时，install() 会打印提示并放弃打补丁，
      程序仍可正常使用（只是不再自动下载），不会因为本模块报错而启动失败。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["install", "load_config", "CONFIG_FILE"]

CONFIG_FILE = Path(__file__).with_name("config.json")
SECTION = "search_auto_download"
DEFAULT_CONFIG: dict[str, Any] = {
    "enable": False,
    "tabs": [0, 1],
    "folder_name": "搜索_{keyword}",
    "default_pages": 0,
    "echo_params": True,
}

INSTALLED = False


def load_config() -> dict[str, Any]:
    """读取开关配置；文件缺失或损坏时回退到默认值（关闭）。"""
    config = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return config
    except (OSError, json.JSONDecodeError) as e:
        print(f"[my_extend] 读取 {CONFIG_FILE.name} 失败，已使用默认配置（关闭）：{e}")
        return config
    section = raw.get(SECTION)
    if isinstance(section, dict):
        config.update({k: v for k, v in section.items() if k in DEFAULT_CONFIG})
    return config


def _find_owner_class() -> type | None:
    """定位定义了 deal_search_data 的类，避免写死类名（上游改过类名）。"""
    from src.application import main_terminal

    for obj in vars(main_terminal).values():
        if isinstance(obj, type) and hasattr(obj, "deal_search_data"):
            return obj
    return None


def _unique_works(data: list) -> list[dict]:
    """按作品 ID 去重，并剔除没有下载地址的条目。"""
    seen: set[str] = set()
    result: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        id_ = item.get("id")
        if not id_ or not item.get("downloads") or id_ in seen:
            continue
        seen.add(str(id_))
        result.append(item)
    return result


async def _maybe_download(owner, model, data, source: bool) -> None:
    """在搜索采集结束后按需下载作品文件。"""
    config = load_config()
    if not config["enable"] or source:
        return
    if not isinstance(data, list) or not data:
        return
    if getattr(model, "channel", None) not in set(config["tabs"]):
        return

    downloader = getattr(owner, "downloader", None)
    parameter = getattr(owner, "parameter", None)
    if downloader is None or parameter is None:
        return
    if not getattr(downloader, "download", False):
        return

    items = _unique_works(data)
    if not items:
        return

    keyword = getattr(model, "keyword", "")
    template = config.get("folder_name") or ""
    folder_name = ""
    if template:
        folder_name = parameter.CLEANER.filter_name(
            template.replace("{keyword}", keyword),
            "搜索_未命名",
        )

    original = downloader.folder_name
    if folder_name:
        downloader.folder_name = folder_name
    try:
        owner.logger.info(
            f"[my_extend] 开始下载搜索到的作品，共 {len(items)} 个",
        )
        await downloader.run(items, "detail")
    finally:
        downloader.folder_name = original


def install() -> bool:
    """给 deal_search_data 打补丁；返回是否安装成功。重复调用无副作用。"""
    global INSTALLED
    if INSTALLED:
        return True

    owner_class = _find_owner_class()
    if owner_class is None:
        print(
            "[my_extend] 未找到 deal_search_data，搜索自动下载不会生效"
            "（上游结构可能已变化），程序仍可正常使用。"
        )
        return False

    original = owner_class.deal_search_data
    if getattr(original, "__my_extend_patched__", False):
        INSTALLED = True
        return True

    async def deal_search_data(self, model, source=False):
        data = await original(self, model, source=source)
        try:
            await _maybe_download(self, model, data, source)
        except Exception as e:  # 下载失败不影响采集结果
            message = f"[my_extend] 搜索自动下载失败：{type(e).__name__}: {e}"
            logger = getattr(self, "logger", None)
            logger.error(message) if logger else print(message)
        return data

    deal_search_data.__my_extend_patched__ = True
    deal_search_data.__wrapped__ = original
    owner_class.deal_search_data = deal_search_data

    _patch_criteria(owner_class)
    _patch_search_run()

    INSTALLED = True
    config = load_config()
    state = "已启用" if config["enable"] else "未启用（如需启用请修改 config.json）"
    print(f"[my_extend] 搜索自动下载补丁已安装：{state}")
    return True


def _echo_params(criteria: list) -> None:
    """回显解析后的搜索参数，便于确认页数是否被正确识别。"""
    keyword = str(criteria[0]).strip() if criteria else ""
    pages = criteria[1] if len(criteria) > 1 else 1
    try:
        rest = list(criteria[2:9])
    except Exception:
        rest = []
    print(f"[my_extend] 搜索参数: 关键词={keyword!r} 页数={pages} 其余参数={rest}")
    tail = keyword.split()[-1] if " " in keyword else ""
    if tail.isdigit():
        print(
            "[my_extend] 注意: 关键词里带着数字，且没有两个连续空格 —— "
            "参数之间必须用【两个空格】分隔，否则页数会被并进关键词，只搜一页。"
        )


def _patch_criteria(owner_class: type) -> None:
    """补上默认页数，并在控制台回显最终参数。"""
    original = getattr(owner_class, "fill_search_criteria", None)
    if original is None or getattr(original, "__my_extend_patched__", False):
        return

    def fill_search_criteria(criteria):
        config = load_config()
        keys = list(criteria)
        default_pages = config.get("default_pages") or 0
        if len(keys) == 1 and default_pages > 0:
            keys.append(default_pages)
        result = original(keys)
        if config.get("echo_params", True):
            _echo_params(result)
        return result

    fill_search_criteria.__my_extend_patched__ = True
    fill_search_criteria.__wrapped__ = original
    owner_class.fill_search_criteria = staticmethod(fill_search_criteria)


def _patch_search_run() -> None:
    """统计每次搜索真正拿到多少条，用来区分「参数没生效」和「平台只给了这么多」。"""
    try:
        from src.interface.search import Search
    except Exception:
        return
    original = Search.run
    if getattr(original, "__my_extend_patched__", False):
        return

    async def run(self, *args, **kwargs):
        pages = getattr(self, "pages", 0)
        result = await original(self, *args, **kwargs)
        if load_config().get("echo_params", True):
            total = len(result) if isinstance(result, list) else 0
            print(
                f"[my_extend] 搜索完成: 关键词={getattr(self, 'keyword', '')!r} "
                f"设定页数={pages} 实际返回 {total} 条作品"
            )
            if pages > 1 and total <= 12:
                print(
                    "[my_extend] 注意: 设定页数远大于实际返回 —— "
                    "接口只给了第一页，通常是风控限流，或该关键词的结果已经到底。"
                )
        return result

    run.__my_extend_patched__ = True
    run.__wrapped__ = original
    Search.run = run

