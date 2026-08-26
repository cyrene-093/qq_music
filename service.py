"""QQ 音乐点歌：搜索、音源、完整歌词展示（不分页）。"""

from __future__ import annotations

import base64
import logging
import re
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from core.network.http_compat import AsyncHttpClient

log = logging.getLogger('plugins.qq_music')

_API = 'https://a.aa.cab/qq.music'
_QQ_LYRIC = 'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg'
_QQ_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://y.qq.com',
}
_STRIP_TBL = str.maketrans('', '', '"\'<>&*_~`[](){}\\/:')
_CACHE_CAP = 100
_SEARCH_TTL_SEC = 180
_SEARCH_LIMIT = 10
_COVER_IMG_SIZE = 22
_PLAY_COVER_SIZE = 120
_LYRIC_TRANS_MAX_LINES = 80
_LRC_LINE_RE = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)')
_META_PREFIXES = (
    '作词', '作曲', '编曲', '制作人', '混音', '录音', '监制', '统筹',
    'Lyrics by', 'Composed by', 'Written by', 'Produced by',
)

_client: AsyncHttpClient | None = None
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_KW_TTL_SEC = 600
_KW_CAP = 200


@dataclass
class LyricBundle:
    lines: list[tuple[float, str]] = field(default_factory=list)
    trans_lines: list[tuple[float, str]] = field(default_factory=list)


def hyperlink(label: str, command: str, *, group_chat: bool = True) -> str:
    cmd = (command or '').strip()
    if not cmd.startswith('#') and not cmd.startswith('＃'):
        cmd = f'#{cmd}'
    enc_cmd = urllib.parse.quote(cmd, safe='')
    enc_show = urllib.parse.quote((label or '').strip(), safe='')
    if not enc_show:
        return ''
    return f'<qqbot-cmd-input text="{enc_cmd}" show="{enc_show}" reference="false" />'


def _cache_key(uid: str, appid: str, group_id: str = '') -> str:
    uid = (uid or '').strip()
    appid = (appid or '').strip()
    gid = (group_id or '').strip()
    if gid:
        return f'{appid}:{gid}:{uid}'
    return f'{appid}:c2c:{uid}'


async def _http() -> AsyncHttpClient:
    global _client
    if _client is None or _client.is_closed:
        _client = AsyncHttpClient(timeout=10.0)
    return _client


async def aclose() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _api(params: str) -> Any:
    c = await _http()
    resp = await c.get(f'{_API}?{params}')
    body = resp.json()
    if not isinstance(body, dict):
        return None
    return body.get('data')


def _clear_search_cache(uid: str, appid: str, group_id: str = '') -> None:
    key = _cache_key(uid, appid, group_id)
    _cache.pop(key, None)


_kw_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _search_cache_get(uid: str, appid: str, group_id: str = '') -> dict[str, Any] | None:
    key = _cache_key(uid, appid, group_id)
    if key not in _cache:
        return None
    info = _cache[key]
    stored_at = info.get('stored_at')
    if not isinstance(stored_at, (int, float)):
        _clear_search_cache(uid, appid, group_id)
        return None
    if time.monotonic() - stored_at > _SEARCH_TTL_SEC:
        _clear_search_cache(uid, appid, group_id)
        return None
    _cache.move_to_end(key)
    return info


def _cache_put(uid: str, appid: str, group_id: str, info: dict[str, Any]) -> None:
    key = _cache_key(uid, appid, group_id)
    if key in _cache:
        _cache.move_to_end(key)
    _cache[key] = info
    while len(_cache) > _CACHE_CAP:
        _cache.popitem(last=False)


def _kw_cache_get(keyword: str) -> list[dict[str, Any]] | None:
    """关键词级搜索结果缓存：同一关键词（任意用户）在 TTL 内直接命中，免外部 API。"""
    info = _kw_cache.get(keyword)
    if not info:
        return None
    stored_at = info.get('stored_at')
    if not isinstance(stored_at, (int, float)) or time.monotonic() - stored_at > _KW_TTL_SEC:
        _kw_cache.pop(keyword, None)
        return None
    _kw_cache.move_to_end(keyword)
    return info.get('songs') or []


def _kw_cache_put(keyword: str, songs: list[dict[str, Any]]) -> None:
    if keyword in _kw_cache:
        _kw_cache.move_to_end(keyword)
    _kw_cache[keyword] = {'songs': songs, 'stored_at': time.monotonic()}
    while len(_kw_cache) > _KW_CAP:
        _kw_cache.popitem(last=False)


def _clean_show(text: str, limit: int = 50) -> str:
    return (text or '未知').translate(_STRIP_TBL).strip()[:limit] or '未知'


def _cover_image_md(cover: str, alt: str, *, size: int = _COVER_IMG_SIZE) -> str:
    """QQ 频道 Markdown 封面：`![标题 #WxH](url)`。"""
    url = (cover or '').strip()
    if not url.startswith(('http://', 'https://')):
        return ''
    label = _clean_show(alt, 24) or '封面'
    return f'![{label} #{size}px #{size}px]({url})'


def _album_cover_url(album_mid: str) -> str:
    mid = (album_mid or '').strip()
    if not mid:
        return ''
    return f'https://y.gtimg.cn/music/photo_new/T002R300x300M000{mid}.jpg'


def store_search(
    uid: str,
    appid: str,
    group_id: str,
    keyword: str,
    songs: list[dict[str, Any]],
) -> None:
    _cache_put(
        uid,
        appid,
        group_id,
        {
            'keyword': keyword,
            'count': len(songs),
            'songs': songs,
            'stored_at': time.monotonic(),
        },
    )


def _decode_lrc_b64(raw_b64: str) -> str:
    if not raw_b64:
        return ''
    return base64.b64decode(raw_b64).decode('utf-8', errors='replace')


def _should_skip_lyric_line(lyric: str) -> bool:
    if any(lyric.startswith(p) for p in _META_PREFIXES):
        return True
    low = lyric.lower()
    return 'lyrics by' in low or 'composed by' in low


def _parse_lrc(text: str) -> list[tuple[float, str]]:
    lines: list[tuple[float, str]] = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        m = _LRC_LINE_RE.match(line)
        if not m:
            continue
        sec = int(m.group(1)) * 60 + float(m.group(2))
        lyric = (m.group(3) or '').strip()
        if not lyric or _should_skip_lyric_line(lyric):
            continue
        lines.append((sec, lyric))
    return lines


async def fetch_qq_lyrics(songmid: str) -> LyricBundle:
    mid = (songmid or '').strip()
    if not mid:
        return LyricBundle()
    try:
        c = await _http()
        resp = await c.get(
            _QQ_LYRIC,
            params={'format': 'json', 'songmid': mid},
            headers=_QQ_HEADERS,
        )
        body = resp.json()
        if not isinstance(body, dict):
            return LyricBundle()
        lines = _parse_lrc(_decode_lrc_b64(body.get('lyric') or ''))
        trans_raw = body.get('trans') or ''
        trans_lines = _parse_lrc(_decode_lrc_b64(trans_raw)) if trans_raw else []
        return LyricBundle(lines=lines, trans_lines=trans_lines)
    except Exception:
        log.debug('歌词获取失败 mid=%s', mid)
        return LyricBundle()


def _trans_at(trans_lines: list[tuple[float, str]], sec: float) -> str | None:
    key = round(sec, 1)
    for t_sec, text in trans_lines:
        if round(t_sec, 1) == key:
            return text
    return None


def can_show_trans_in_markdown(bundle: LyricBundle) -> bool:
    return bool(bundle.trans_lines) and len(bundle.trans_lines) <= _LYRIC_TRANS_MAX_LINES


def _lyric_lines_for_code(
    chunk: list[tuple[float, str]],
    bundle: LyricBundle,
    *,
    show_trans: bool,
) -> list[str]:
    out: list[str] = []
    for sec, text in chunk:
        mm = int(sec) // 60
        ss = int(sec) % 60
        safe = _clean_show(text, 80)
        out.append(f'[{mm:02d}:{ss:02d}] {safe}')
        if show_trans:
            cn = _trans_at(bundle.trans_lines, sec)
            if cn:
                out.append(f'    {_clean_show(cn, 80)}')
    return out


_LYRIC_MAX_CHARS = 3500


def _lyric_line_text(sec: float, text: str, bundle: LyricBundle, *, show_trans: bool) -> str:
    mm = int(sec) // 60
    ss = int(sec) % 60
    safe = _clean_show(text, 80)
    line = f'[{mm:02d}:{ss:02d}] {safe}'
    if show_trans:
        cn = _trans_at(bundle.trans_lines, sec)
        if cn:
            line += f'\n      {_clean_show(cn, 80)}'
    return line


def _song_cover_url(data: dict[str, Any]) -> str:
    cover = str(data.get('cover') or '').strip()
    if cover.startswith(('http://', 'https://')):
        return cover
    return _album_cover_url(str(data.get('album_mid') or ''))


def build_lyric_lrc_text(
    bundle: LyricBundle,
    *,
    max_chars: int = _LYRIC_MAX_CHARS,
) -> str:
    """LRC 纯文本（用于代码块内）。"""
    lines = bundle.lines
    if not lines:
        return '暂无歌词'
    show_trans = can_show_trans_in_markdown(bundle)
    body: list[str] = []
    used = 0
    truncated = False
    for sec, text in lines:
        piece = _lyric_line_text(sec, text, bundle, show_trans=show_trans)
        if used + len(piece) + 1 > max_chars:
            truncated = True
            break
        body.append(piece)
        used += len(piece) + 1
    code = '\n'.join(body)
    if truncated:
        tail = f'\n……（歌词过长已截断，共 {len(lines)} 行，仅显示前 {len(body)} 行）'
        code = (code + tail)[:max_chars]
    return code


def format_play_detail_markdown(
    data: dict[str, Any],
    lyric_bundle: LyricBundle | None = None,
    *,
    show_lyrics: bool = True,
    lyrics_hint: str = '',
    music_url: str = '',
) -> str:
    """播放详情：封面 + 歌手/专辑 + LRC 代码块（单条消息）。"""
    song = _clean_show(str(data.get('song') or ''))
    singer = _clean_show(str(data.get('singer') or ''))
    album = _clean_show(str(data.get('album_name') or ''))
    cover = _song_cover_url(data)
    cover_md = _cover_image_md(cover, song, size=_PLAY_COVER_SIZE)

    lines: list[str] = [f'## 🎵 {song}', '──────────────']
    if cover_md:
        lines.extend(['', cover_md])
    lines.extend([
        '',
        f'**歌手**：{singer}',
        f'**专辑**：{album or "未知"}',
    ])
    bundle = lyric_bundle or LyricBundle()
    if show_lyrics and bundle.lines:
        lines.extend(['', f'```\n{build_lyric_lrc_text(bundle)}\n```'])
    elif lyrics_hint:
        lines.extend(['', lyrics_hint])
    elif bundle.lines and not show_lyrics:
        lines.extend(['', '（歌词已关闭，发送 `#歌词开` 可在播放时显示歌词）'])
    if music_url:
        lines.extend(['', f'[备用播放链接]({music_url})'])
    return '\n'.join(lines)


def build_lyric_markdown(bundle: LyricBundle, song_name: str = '', *, max_chars: int = _LYRIC_MAX_CHARS) -> str:
    """兼容旧调用：仅歌词代码块。"""
    title_base = _clean_show(song_name) or '未知'
    body = build_lyric_lrc_text(bundle, max_chars=max_chars)
    return f'### {title_base} · 歌词\n\n```\n{body}\n```'


def store_last_lyric(uid: str, appid: str, group_id: str, song: str, markdown: str) -> None:
    """已废弃：歌词并入播放详情，保留空实现避免旧代码引用报错。"""
    return


def get_last_lyric(uid: str, appid: str, group_id: str) -> dict[str, Any] | None:
    return None


async def search_songs(keyword: str) -> list[dict[str, Any]]:
    kw = re.sub(r'\s+', ' ', (keyword or '').strip())
    if not kw:
        return []
    cached = _kw_cache_get(kw)
    if cached is not None:
        return cached
    songs = await _api(f'msg={urllib.parse.quote(kw)}')
    if not isinstance(songs, list):
        return []
    out: list[dict[str, Any]] = []
    for item in songs[:_SEARCH_LIMIT]:
        if not isinstance(item, dict):
            continue
        out.append({
            'num': int(item.get('num') or len(out) + 1),
            'song': str(item.get('song') or '未知'),
            'singer': str(item.get('singer') or '未知'),
            'album_name': str(item.get('album_name') or ''),
            'album_mid': str(item.get('album_mid') or ''),
            'cover': str(item.get('cover') or ''),
        })
    _kw_cache_put(kw, out)
    return out


PLAY_ERR_EXPIRED = 'expired'
PLAY_ERR_BAD_INDEX = 'bad_index'
PLAY_ERR_API = 'api'


async def fetch_play_data(
    uid: str,
    appid: str,
    group_id: str,
    index: int,
) -> tuple[dict[str, Any] | None, str | None]:
    info = _search_cache_get(uid, appid, group_id)
    if not info:
        return None, PLAY_ERR_EXPIRED
    count = int(info.get('count') or 0)
    if index < 1 or index > count:
        return None, PLAY_ERR_BAD_INDEX
    keyword = str(info.get('keyword') or '')
    data = await _api(f'msg={urllib.parse.quote(keyword)}&n={index}')
    if not isinstance(data, dict):
        return None, PLAY_ERR_API
    return data, None


async def fetch_play_with_lyric(
    uid: str,
    appid: str,
    group_id: str,
    index: int,
) -> tuple[dict[str, Any] | None, LyricBundle, str | None]:
    data, err = await fetch_play_data(uid, appid, group_id, index)
    if not data:
        return None, LyricBundle(), err
    mid = str(data.get('mid') or '').strip()
    lyrics = await fetch_qq_lyrics(mid)
    return data, lyrics, None


def format_search_list(
    songs: list[dict[str, Any]],
    keyword: str,
    *,
    group_chat: bool = True,
) -> str:
    if not songs:
        return f'## 🎵 点歌\n未找到「{keyword}」相关歌曲，请换关键词。'
    lines = [
        f'## 🎵 QQ音乐 · 「{keyword}」',
        '──────────────',
        '点击蓝字播放（`#听1`），列表 **3 分钟内**有效：',
        '',
    ]
    for i, s in enumerate(songs, 1):
        name = _clean_show(s.get('song', ''))
        singer = _clean_show(s.get('singer', ''), 30)
        album = _clean_show(s.get('album_name', ''), 24)
        cover = str(s.get('cover') or '').strip() or _album_cover_url(str(s.get('album_mid') or ''))
        cover_md = _cover_image_md(cover, name)
        link = hyperlink(f'{i}.{name}', f'#听{i}', group_chat=group_chat)
        tail = f' · {singer}'
        if album:
            tail += f' · {album}'
        row = f'{link}{tail}'
        if cover_md:
            lines.append(f'{cover_md} {row}')
        else:
            lines.append(row)

    return '\n'.join(lines)


def format_play_markdown(data: dict[str, Any], *, music_url: str = '') -> str:
    song = _clean_show(str(data.get('song') or ''))
    singer = _clean_show(str(data.get('singer') or ''))
    album = _clean_show(str(data.get('album_name') or ''))
    lines = [
        f'## 🎵 {song}',
        '──────────────',
        f'**歌手**：{singer}',
        f'**专辑**：{album or "未知"}',
    ]
    if music_url:
        lines.extend(['', f'[备用播放链接]({music_url})'])
    return '\n'.join(lines)


def format_help_markdown() -> str:
    return (
        '## 🎵 音乐点歌\n'
        '──────────────\n'
        '- `#点歌 歌名` 搜索列表（纯数字歌名如 2077 也按歌名搜索）\n'
        '- `#听1` 播放列表第 1 首（含封面、歌手、专辑与 LRC 歌词）\n'
        '- `#歌词开` / `#歌词关` / `#歌词开关` 控制播放时是否附带歌词\n'
        '- `#群歌词开` / `#群歌词关` 群主/管理员控制本群歌词\n'
        '- 搜索列表 **3 分钟**内有效，过期请重新 `#点歌`\n'
        '- 优先发语音条，过长时自动改为文件\n'
        '- 音源：QQ音乐'
    )


def search_buttons() -> list[list[dict[str, Any]]]:
    return [
        [
            {'text': '再搜一首', 'data': '#点歌', 'type': 2, 'enter': False, 'style': 2},
            {'text': '歌词开关', 'data': 'qm:lyric:toggle', 'type': 1, 'style': 2},
            {'text': '音乐帮助', 'data': '#音乐帮助', 'type': 2, 'enter': True, 'style': 4},
        ],
        [
            {'text': '群歌词开关', 'data': 'qm:group_lyric:toggle', 'type': 1, 'style': 3},
        ],
    ]


def play_buttons() -> list[list[dict[str, Any]]]:
    return [
        [
            {'text': '再搜一首', 'data': '#点歌', 'type': 2, 'enter': False, 'style': 1},
            {'text': '点歌稻香', 'data': '#点歌 稻香', 'type': 2, 'enter': True, 'style': 2},
        ],
        [
            {'text': '歌词开关', 'data': 'qm:lyric:toggle', 'type': 1, 'style': 2},
            {'text': '群歌词开关', 'data': 'qm:group_lyric:toggle', 'type': 1, 'style': 3},
            {'text': '音乐帮助', 'data': '#音乐帮助', 'type': 2, 'enter': True, 'style': 4},
        ],
    ]


def help_buttons() -> list[list[dict[str, Any]]]:
    return [
        [
            {'text': '点歌稻香', 'data': '#点歌 稻香', 'type': 2, 'enter': True, 'style': 1},
            {'text': '音乐帮助', 'data': '#音乐帮助', 'type': 2, 'enter': True, 'style': 4},
        ],
        [
            {'text': '歌词开关', 'data': 'qm:lyric:toggle', 'type': 1, 'style': 2},
            {'text': '群歌词开关', 'data': 'qm:group_lyric:toggle', 'type': 1, 'style': 3},
        ],
    ]
