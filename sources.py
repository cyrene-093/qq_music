"""可切换点歌音源：默认第三方，可选官方搜索 / 落月 / 网易云。"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger('plugins.qq_music.sources')

DEFAULT_SOURCE_ID = 'aa_qq'


@dataclass(frozen=True)
class MusicSource:
    id: str
    name: str
    platform: str
    kind: str
    search_url: str
    play_url: str = ''
    lyric_url: str = ''
    quality: int = 8
    aliases: tuple[str, ...] = field(default_factory=tuple)
    note: str = ''
    short: str = ''
    badge: str = ''


PRESETS: tuple[MusicSource, ...] = (
    MusicSource(
        id='aa_qq',
        name='QQ音乐（第三方）',
        platform='qq',
        kind='msg_n',
        search_url='https://a.aa.cab/qq.music?msg={msg}',
        play_url='https://a.aa.cab/qq.music?msg={msg}&n={n}',
        aliases=('第三方', 'aa', '个人站', '默认', '默认接口', '原版'),
        short='第三方',
        badge='默认',
        note='默认源。搜播一体，依赖第三方站点可用性与对方曲库规则；码率与可播范围不保证。',
    ),
    MusicSource(
        id='official_qq',
        name='QQ音乐（官方搜索）',
        platform='qq',
        kind='official_qq',
        search_url='',
        aliases=('官方', '官方qq', 'official', 'qq官方'),
        short='官方搜',
        badge='搜得准',
        note='搜索直连 y.qq.com，列表更接近 QQ 音乐。官方播放直链不可用，播放自动走第三方 / 落月 / 网易云。',
    ),
    MusicSource(
        id='vkeys_qq',
        name='QQ音乐（落月）',
        platform='qq',
        kind='vkeys',
        search_url='https://api.vkeys.cn/v2/music/tencent?word={msg}',
        play_url='https://api.vkeys.cn/v2/music/tencent?id={id}&quality={quality}',
        lyric_url='https://api.vkeys.cn/v2/music/tencent/lyric?id={id}',
        quality=8,
        aliases=('落月', '落月qq', 'vkeys', '落月api'),
        short='落月QQ',
        badge='HQ',
        note='落月 api.vkeys.cn。常为较高音质；部分曲目可能仅有短时试听，插件会尝试其他接口。',
    ),
    MusicSource(
        id='epdd_qq',
        name='QQ音乐（落月备用域）',
        platform='qq',
        kind='vkeys',
        search_url='https://api.epdd.cn/v2/music/tencent?word={msg}',
        play_url='https://api.epdd.cn/v2/music/tencent?id={id}&quality={quality}',
        lyric_url='https://api.epdd.cn/v2/music/tencent/lyric?id={id}',
        quality=8,
        aliases=('落月备用', 'epdd', 'epddqq', '备用域'),
        short='落月备',
        badge='镜像',
        note='落月备用域名 api.epdd.cn，协议与落月 QQ 相同。主域不可用时可切换。',
    ),
    MusicSource(
        id='vkeys_netease',
        name='网易云（落月）',
        platform='netease',
        kind='vkeys',
        search_url='https://api.vkeys.cn/v2/music/netease?word={msg}',
        play_url='https://api.vkeys.cn/v2/music/netease?id={id}&quality={quality}',
        lyric_url='https://api.vkeys.cn/v2/music/netease/lyric?id={id}',
        quality=4,
        aliases=('网易', '网易云', 'ne', '163', 'netease'),
        short='网易云',
        badge='换平台',
        note='网易云曲库。部分歌曲可能无直链；与 QQ 曲库不完全对应。',
    ),
    MusicSource(
        id='epdd_netease',
        name='网易云（落月备用域）',
        platform='netease',
        kind='vkeys',
        search_url='https://api.epdd.cn/v2/music/netease?word={msg}',
        play_url='https://api.epdd.cn/v2/music/netease?id={id}&quality={quality}',
        lyric_url='https://api.epdd.cn/v2/music/netease/lyric?id={id}',
        quality=4,
        aliases=('网易备用', 'epdd网易', 'epddne'),
        short='网易备',
        badge='镜像',
        note='网易云备用域名。主域不可用时的退路。',
    ),
)

_BY_ID = {s.id: s for s in PRESETS}


def known_source_ids(custom_url: str = '') -> set[str]:
    ids = set(_BY_ID)
    if (custom_url or '').strip():
        ids.add('custom')
    return ids


def list_sources(custom_url: str = '') -> list[dict[str, Any]]:
    rows = [
        {
            'id': s.id,
            'name': s.name,
            'short': s.short or s.name,
            'badge': s.badge,
            'platform': s.platform,
            'kind': s.kind,
            'note': s.note,
            'aliases': list(s.aliases),
        }
        for s in PRESETS
    ]
    url = (custom_url or '').strip()
    if url:
        custom = _custom_source(url)
        rows.append({
            'id': custom.id,
            'name': custom.name,
            'short': custom.short or '自定义',
            'badge': custom.badge or '自定义',
            'platform': custom.platform,
            'kind': custom.kind,
            'note': custom.note,
            'aliases': list(custom.aliases),
        })
    return rows


def get_source(source_id: str, custom_url: str = '') -> MusicSource:
    sid = (source_id or '').strip() or DEFAULT_SOURCE_ID
    if sid == 'custom':
        url = (custom_url or '').strip()
        if url:
            return _custom_source(url)
        sid = DEFAULT_SOURCE_ID
    return _BY_ID.get(sid) or _BY_ID[DEFAULT_SOURCE_ID]


def resolve_source(text: str, custom_url: str = '') -> MusicSource | None:
    raw = (text or '').strip()
    if not raw:
        return None
    low = raw.lower()
    custom = (custom_url or '').strip()
    if low in ('custom', '自定义') and custom:
        return _custom_source(custom)
    if raw in _BY_ID:
        return _BY_ID[raw]
    if low in _BY_ID:
        return _BY_ID[low]
    if raw.isdigit():
        idx = int(raw)
        rows = list(PRESETS)
        if custom:
            rows.append(_custom_source(custom))
        if 1 <= idx <= len(rows):
            return rows[idx - 1]
        return None
    # 精确匹配：别名 / 短名 / 全名，避免「音乐」「qq」模糊命中错源
    candidates = list(PRESETS)
    if custom:
        candidates.append(_custom_source(custom))
    for src in candidates:
        if low == src.id.lower() or low == src.name.lower() or low == (src.short or '').lower():
            return src
        if any(low == a.lower() for a in src.aliases):
            return src
    return None


def _custom_source(url: str) -> MusicSource:
    tpl = url.strip()
    if '{msg}' not in tpl:
        sep = '&' if '?' in tpl else '?'
        tpl = f'{tpl}{sep}msg={{msg}}'
    play = tpl if '{n}' in tpl else (
        f'{tpl}&n={{n}}' if '?' in tpl else f'{tpl}?n={{n}}'
    )
    return MusicSource(
        id='custom',
        name='自定义接口',
        platform='other',
        kind='msg_n',
        search_url=tpl.replace('&n={n}', '').replace('n={n}&', '').replace('?n={n}', ''),
        play_url=play,
        aliases=('自定义', 'custom'),
        short='自定义',
        badge='自定义',
        note=f'兼容 msg/n 的第三方接口。当前：{tpl}',
    )


def _fmt(url: str, **kwargs: Any) -> str:
    safe: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in ('msg', 'id', 'mid', 'word'):
            safe[k] = urllib.parse.quote(str(v), safe='')
        else:
            safe[k] = str(v)

    class _Map(dict):
        def __missing__(self, key: str) -> str:
            return ''

    try:
        return url.format_map(_Map(safe))
    except Exception:
        return url


async def _get_json(url: str) -> Any:
    from plugins.qq_music.service import _http

    c = await _http()
    resp = await c.get(url)
    if getattr(resp, 'status_code', 200) >= 400:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    if isinstance(body, dict):
        code = body.get('code')
        if code is not None:
            try:
                if int(code) not in (0, 200):
                    return None
            except (TypeError, ValueError):
                pass
    return body


def _unwrap(body: Any) -> Any:
    if not isinstance(body, dict):
        return body
    if 'data' in body:
        return body.get('data')
    return body


def _same_platform(src: MusicSource, item: dict[str, Any]) -> bool:
    item_src = str(item.get('source') or '').strip()
    if not item_src or item_src == src.id:
        return True
    other = _BY_ID.get(item_src)
    if other and other.platform == src.platform:
        return True
    return False


def _same_source_list(src: MusicSource, item: dict[str, Any]) -> bool:
    """同一歌源搜出的列表，序号才可直接用于播放。"""
    item_src = str(item.get('source') or '').strip()
    return (not item_src) or item_src == src.id


def _singer_text(item: dict[str, Any]) -> str:
    singer = item.get('singer')
    if isinstance(singer, list):
        parts = []
        for x in singer:
            if isinstance(x, dict):
                parts.append(str(x.get('name') or x.get('title') or ''))
            else:
                parts.append(str(x))
        singer = '/'.join(p for p in parts if p)
    if not singer:
        sl = item.get('singer_list') or item.get('singerList')
        if isinstance(sl, list):
            singer = '/'.join(str(x.get('name') or '') for x in sl if isinstance(x, dict))
    return str(singer or '未知')


def _norm_search_item(src: MusicSource, item: dict[str, Any], num: int) -> dict[str, Any]:
    return {
        'num': num,
        'song': str(item.get('song') or item.get('title') or item.get('name') or '未知'),
        'singer': _singer_text(item),
        'album_name': str(item.get('album_name') or item.get('album') or ''),
        'album_mid': str(item.get('album_mid') or ''),
        'cover': str(
            item.get('cover')
            or item.get('pic')
            or item.get('picture')
            or item.get('albumImage')
            or ''
        ),
        'mid': str(item.get('mid') or item.get('songMID') or ''),
        'id': str(item.get('id') or item.get('songID') or item.get('songid') or ''),
        'interval': item.get('interval') or item.get('songTimeMinutes') or '',
        'source': src.id,
    }


def _norm_play(src: MusicSource, data: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    music = str(data.get('music') or data.get('url') or data.get('mp3') or data.get('src') or '').strip()
    return {
        'song': str(data.get('song') or item.get('song') or '未知'),
        'singer': _singer_text(data) if data.get('singer') or data.get('singer_list') else str(item.get('singer') or '未知'),
        'album_name': str(data.get('album_name') or data.get('album') or item.get('album_name') or ''),
        'album_mid': str(data.get('album_mid') or item.get('album_mid') or ''),
        'cover': str(data.get('cover') or data.get('pic') or item.get('cover') or ''),
        'music': music,
        'mid': str(data.get('mid') or item.get('mid') or ''),
        'id': str(data.get('id') or item.get('id') or ''),
        'quality': str(data.get('quality') or data.get('tips') or ''),
        'interval': data.get('interval') or item.get('interval') or '',
        'size': data.get('size') or '',
        'kbps': data.get('kbps') or '',
        '_source': src.id,
        '_source_name': src.name,
        '_trial': False,
    }


_PREVIEW_MP3 = (850_000, 1_150_000)
_TRIAL_BITRATE = 72_000
_SKIP_TITLE = (
    'remix', 'sped', 'nightcore', 'cover', '翻唱', '夜店', '电音', '抖音',
    '女声', '男声', '伴奏', '铃声', 'live', '现场', 'montagem',
)
_FALLBACK = {
    'official_qq': ('aa_qq', 'vkeys_qq', 'epdd_qq', 'vkeys_netease', 'epdd_netease'),
    'aa_qq': ('vkeys_qq', 'epdd_qq', 'vkeys_netease', 'epdd_netease'),
    'vkeys_qq': ('aa_qq', 'epdd_qq', 'vkeys_netease', 'epdd_netease'),
    'epdd_qq': ('aa_qq', 'vkeys_qq', 'vkeys_netease', 'epdd_netease'),
    'vkeys_netease': ('epdd_netease',),
    'epdd_netease': ('vkeys_netease',),
    'custom': ('aa_qq', 'vkeys_qq', 'epdd_qq'),
}


def _parse_interval_sec(raw: Any) -> int:
    if isinstance(raw, (int, float)):
        n = int(raw)
        return n if n >= 20 else 0
    text = str(raw or '').strip()
    if not text:
        return 0
    m = re.match(r'(\d+)\s*分\s*(\d+(?:\.\d+)?)\s*秒', text)
    if m:
        return int(m.group(1)) * 60 + int(float(m.group(2)))
    m = re.match(r'(\d+):(\d+)(?::(\d+))?$', text)
    if m:
        if m.group(3) is not None:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r'(\d+)', text)
    if m:
        n = int(m.group(1))
        return n if n >= 20 else 0
    return 0


def _parse_size_bytes(raw: Any) -> int:
    if isinstance(raw, (int, float)) and raw > 4096:
        return int(raw)
    text = str(raw or '').strip().upper().replace(' ', '')
    m = re.match(r'([\d.]+)(GB|MB|KB|B)?', text)
    if not m:
        return 0
    n = float(m.group(1))
    unit = m.group(2) or ''
    if unit == 'GB':
        return int(n * 1024 ** 3)
    if unit == 'MB':
        return int(n * 1024 ** 2)
    if unit == 'KB':
        return int(n * 1024)
    if n > 10_000:
        return int(n)
    return 0


def _parse_kbps(raw: Any) -> int:
    if isinstance(raw, (int, float)):
        return int(raw)
    m = re.search(r'(\d+)', str(raw or ''))
    return int(m.group(1)) if m else 0


def _fold_text(raw: Any) -> str:
    return re.sub(r'\s+', '', str(raw or '').lower())


def _title_core(raw: Any) -> str:
    text = re.sub(r'[\(\（\[【].*?[\)\）\]】]', '', str(raw or ''))
    return _fold_text(text)


def _primary_singer(raw: Any) -> str:
    text = _singer_text({'singer': raw})
    for sep in ('/', '、', '&', ',', 'feat.', 'feat', 'ft.'):
        text = text.split(sep)[0]
    return _fold_text(text.strip())


def _is_cover_or_remix(item: dict[str, Any], cand: dict[str, Any]) -> bool:
    orig = _fold_text(item.get('song'))
    title = _fold_text(cand.get('song'))
    singer = _fold_text(cand.get('singer'))
    for word in _SKIP_TITLE:
        if word in title and word not in orig:
            return True
        if word in singer and word not in _fold_text(item.get('singer')):
            return True
    return False


def _track_match(item: dict[str, Any], cand: dict[str, Any]) -> bool:
    """歌名 + 歌手严格匹配（跨源回退用）。"""
    want = _title_core(item.get('song'))
    got = _title_core(cand.get('song'))
    if not want or (want != got and want not in _fold_text(cand.get('song'))):
        return False
    singer = _primary_singer(item.get('singer'))
    if singer and singer not in _fold_text(cand.get('singer')):
        return False
    return not _is_cover_or_remix(item, cand)


def _title_soft_match(item: dict[str, Any], cand: dict[str, Any]) -> bool:
    """仅歌名匹配，排除 remix/翻唱。"""
    want = _title_core(item.get('song'))
    if not want:
        return False
    got = _title_core(cand.get('song'))
    if want != got and want not in _fold_text(cand.get('song')):
        return False
    return not _is_cover_or_remix(item, cand)


def _netease_match(item: dict[str, Any], cand: dict[str, Any]) -> bool:
    return _track_match(item, cand)


def _trial_from_meta(got: dict[str, Any], item: dict[str, Any]) -> bool:
    if str(got.get('quality') or '').find('试听') >= 0:
        return True
    interval = _parse_interval_sec(got.get('interval') or item.get('interval'))
    size = int(got.get('_bytes') or 0) or _parse_size_bytes(got.get('size'))
    kbps = _parse_kbps(got.get('kbps'))
    # 落月常见 ~0.92MB 试听：需同时具备「预览体积」与「短时长/未知时长」
    if size and _PREVIEW_MP3[0] <= size <= _PREVIEW_MP3[1]:
        if not interval or interval <= 90:
            return True
    # 短时长 + 明确小文件
    if 0 < interval <= 75 and size and size < 1_800_000:
        return True
    if interval >= 90:
        if kbps and kbps < 80:
            return True
        if size and (size * 8 / interval) < _TRIAL_BITRATE:
            return True
    return False


async def _range_size(url: str) -> int:
    blob = (url or '').strip()
    if not blob.startswith('http'):
        return 0
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Range': 'bytes=0-0',
    }
    try:
        from plugins.qq_music.service import _http

        c = await _http()
        resp = await c.get(blob, headers=headers, timeout=8.0)
        status = int(getattr(resp, 'status_code', 0) or 0)
        hdrs = getattr(resp, 'headers', None) or {}
        cr = str(hdrs.get('Content-Range') or hdrs.get('content-range') or '')
        cl = str(hdrs.get('Content-Length') or hdrs.get('content-length') or '')
    except Exception:
        log.debug('探测音频大小失败', exc_info=True)
        return 0
    m = re.search(r'/(\d+)\s*$', cr)
    if m:
        return int(m.group(1))
    if status == 200 and cl.isdigit() and int(cl) > 4096:
        return int(cl)
    return 0


async def _annotate_trial(got: dict[str, Any] | None, item: dict[str, Any]) -> dict[str, Any] | None:
    if not got or not str(got.get('music') or '').strip():
        return got
    size = int(got.get('_bytes') or 0) or _parse_size_bytes(got.get('size'))
    if not size:
        size = await _range_size(str(got.get('music') or ''))
    if size:
        got['_bytes'] = size
    trial = _trial_from_meta(got, item)
    got['_trial'] = trial
    if trial:
        quality = str(got.get('quality') or '').strip()
        if '试听' not in quality:
            got['quality'] = f'试听 · {quality}'.strip(' ·') if quality else '试听'
    return got


def _mark_fallback(got: dict[str, Any], origin: MusicSource, *, full: bool) -> dict[str, Any]:
    """标注备用播放：保留实际播放源 id（供歌词拉取），展示名带上原始源。"""
    play_id = str(got.get('_source') or '').strip()
    play_name = str(got.get('_source_name') or '').strip()
    got['_play_source'] = play_id
    got['_origin_source'] = origin.id
    label = f'{origin.name} · {"完整备用" if full else "备用"}'
    if play_name and play_id and play_id != origin.id:
        label = f'{label}（经 {play_name}）'
    got['_source_name'] = label
    return got


async def search_source(src: MusicSource, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    if src.kind == 'official_qq':
        from plugins.qq_music.official_qq import search_official
        from plugins.qq_music.settings import get_store

        rows = await search_official(keyword, limit, get_store().get_qq_cookie())
        return [_norm_search_item(src, item, i) for i, item in enumerate(rows, 1)]

    url = _fmt(src.search_url, msg=keyword, word=keyword)
    body = await _get_json(url)
    data = _unwrap(body)
    rows: list[Any]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get('list') or data.get('songs') or data.get('itemlist') or []
        if not isinstance(rows, list):
            rows = []
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        out.append(_norm_search_item(src, item, len(out) + 1))
    return out


async def play_source(
    src: MusicSource,
    item: dict[str, Any],
    *,
    keyword: str,
    index: int,
    _fallback: bool = True,
) -> dict[str, Any] | None:
    got = await _play_direct(src, item, keyword=keyword, index=index)
    got = await _annotate_trial(got, item)
    if not _fallback:
        return got
    chain = _FALLBACK.get(src.id) or ()
    if got and str(got.get('music') or '').strip() and not got.get('_trial'):
        return got
    best = got if got and str(got.get('music') or '').strip() else None
    fb_kw = str(item.get('song') or keyword or '').strip() or keyword
    for fb_id in chain:
        fb = get_source(fb_id)
        try:
            # 跨站序号对不齐：msg/n 源统一用歌名取第 1 首；落月用 mid/id 或歌名
            fb_index = 1 if fb.kind == 'msg_n' else index
            alt = await _play_direct(fb, item, keyword=fb_kw, index=fb_index)
            alt = await _annotate_trial(alt, item)
        except Exception:
            log.debug('备用源失败 fallback=%s', fb_id, exc_info=True)
            continue
        if not alt or not str(alt.get('music') or '').strip():
            continue
        if not alt.get('_trial'):
            return _mark_fallback(alt, src, full=True)
        if best is None or int(alt.get('_bytes') or 0) > int(best.get('_bytes') or 0):
            best = alt
    if best:
        full = not bool(best.get('_trial'))
        if best.get('_source') != src.id or src.id == 'official_qq':
            return _mark_fallback(best, src, full=full)
        return best
    return got


async def _play_direct(
    src: MusicSource,
    item: dict[str, Any],
    *,
    keyword: str,
    index: int,
) -> dict[str, Any] | None:
    if src.kind == 'official_qq':
        from plugins.qq_music.official_qq import play_official
        from plugins.qq_music.settings import get_store

        data = await play_official(item, cookie=get_store().get_qq_cookie())
        return _norm_play(src, data, item) if isinstance(data, dict) else None

    if src.platform == 'netease' and not _same_platform(src, item):
        return await _play_netease_match(item, keyword)

    if src.kind == 'msg_n':
        if not _same_source_list(src, item):
            # 跨源回退：按歌名+歌手匹配序号，避免盲取第 1 首
            return await _play_msg_n_match(src, item, keyword)
        url = _fmt(src.play_url or src.search_url, msg=keyword, n=index, word=keyword)
        body = await _get_json(url)
        data = _unwrap(body)
        if not isinstance(data, dict):
            return None
        return _norm_play(src, data, item)

    sid = ''
    mid = ''
    if _same_source_list(src, item):
        sid = str(item.get('id') or '').strip()
        mid = str(item.get('mid') or '').strip()
    elif src.platform == 'qq' and _same_platform(src, item):
        # 官方 / 落月 / 第三方同属 QQ，可用 songmid 换链
        mid = str(item.get('mid') or '').strip()
    qualities: list[int] = []
    for q in (src.quality, 8, 4, 2, 1):
        if q not in qualities:
            qualities.append(q)
    last: dict[str, Any] | None = None
    for q in qualities:
        if sid:
            url = f'{src.search_url.split("?", 1)[0]}?id={urllib.parse.quote(sid)}&quality={q}'
        elif mid:
            url = f'{src.search_url.split("?", 1)[0]}?mid={urllib.parse.quote(mid)}&quality={q}'
        else:
            url = (
                f'{src.search_url.split("?", 1)[0]}'
                f'?word={urllib.parse.quote(keyword)}&choose={index}&quality={q}'
            )
        try:
            body = await _get_json(url)
        except Exception:
            log.debug('音源播放请求失败 source=%s q=%s', src.id, q)
            continue
        data = _unwrap(body)
        if not isinstance(data, dict):
            continue
        last = _norm_play(src, data, item)
        if last.get('music'):
            return last
    return last


def _match_queries(item: dict[str, Any], keyword: str) -> list[str]:
    song = str(item.get('song') or keyword or '').strip()
    singer = _singer_text(item)
    queries: list[str] = []
    if song and singer:
        queries.append(f'{song} {singer.split("/")[0].split("、")[0].strip()}')
    if song and song not in queries:
        queries.append(song)
    kw = (keyword or '').strip()
    if kw and kw not in queries:
        queries.append(kw)
    return queries


def _pick_matched_row(item: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    pick = next((row for row in rows if _track_match(item, row)), None)
    if pick:
        return pick
    return next((row for row in rows if _title_soft_match(item, row)), None)


async def _play_msg_n_match(src: MusicSource, item: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    song = str(item.get('song') or keyword or '').strip()
    used_q = song or keyword
    rows: list[dict[str, Any]] = []
    for q in _match_queries(item, keyword):
        try:
            rows = await search_source(src, q, 10)
        except Exception:
            log.debug('msg_n 匹配搜索失败 source=%s', src.id, exc_info=True)
            rows = []
        if rows:
            used_q = q
            break
    pick = _pick_matched_row(item, rows)
    if not pick:
        return None
    idx = int(pick.get('num') or 1)
    url = _fmt(src.play_url or src.search_url, msg=used_q, n=idx, word=used_q)
    body = await _get_json(url)
    data = _unwrap(body)
    if not isinstance(data, dict):
        return None
    return _norm_play(src, data, pick)


async def _play_netease_match(item: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    # 优先主域，失败再备用域
    for src_id in ('vkeys_netease', 'epdd_netease'):
        src = get_source(src_id)
        song = str(item.get('song') or keyword or '').strip()
        rows: list[dict[str, Any]] = []
        for q in _match_queries(item, keyword):
            try:
                rows = await search_source(src, q, 8)
            except Exception:
                log.debug('网易云匹配搜索失败 source=%s', src_id, exc_info=True)
                rows = []
            if rows:
                break
        pick = _pick_matched_row(item, rows)
        if not pick:
            continue
        got = await _play_direct(src, pick, keyword=song, index=int(pick.get('num') or 1))
        if got and str(got.get('music') or '').strip():
            return got
    return None


async def fetch_source_lrc(src: MusicSource, item: dict[str, Any]) -> str:
    if not src.lyric_url:
        return ''
    sid = str(item.get('id') or '').strip()
    mid = str(item.get('mid') or '').strip()
    url = _fmt(src.lyric_url, id=sid or mid, mid=mid)
    if '{id}' in src.lyric_url and not (sid or mid):
        return ''
    try:
        body = await _get_json(url)
    except Exception:
        return ''
    data = _unwrap(body)
    if isinstance(data, dict):
        return str(data.get('lrc') or data.get('lyric') or data.get('lyrics') or '')
    return str(data or '')


def format_source_help(current: MusicSource, *, group: bool = False, custom_url: str = '') -> str:
    scope = '本群' if group else '全局'
    lines = [
        '## 🎵 歌源切换',
        '──────────────',
        f'当前{scope}：**{current.name}**',
        '',
    ]
    rows = list(PRESETS)
    custom = (custom_url or '').strip()
    if custom:
        rows.append(_custom_source(custom))
    for i, src in enumerate(rows, 1):
        mark = ' ◀' if src.id == current.id else ''
        badge = f' 〔{src.badge}〕' if src.badge else ''
        lines.append(f'{i}. {src.name}{badge}{mark}')
        if src.note:
            lines.append(f'   {src.note}')
    lines.extend([
        '',
        f'发送 `#歌源 1` / `#歌源 落月` / `#歌源 官方` 切换{scope}歌源。',
        '群内还可 `#歌源 跟随` 恢复跟随全局。',
        '默认使用第三方搜索/播放接口；官方搜索更准，播放会自动尝试其他接口。',
        '请遵守法律与版权；音源可用性不保证。',
        '换源后请重新 `#点歌`；已搜出的列表仍按搜索时的源播放。',
    ])
    return '\n'.join(lines)


def source_switch_buttons(custom_url: str = '') -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    row: list[dict[str, Any]] = []
    sources = list(PRESETS)
    if (custom_url or '').strip():
        sources.append(_custom_source(custom_url))
    for src in sources:
        label = (src.short or src.name)[:8]
        row.append({
            'text': label,
            'data': f'#歌源 {src.id}',
            'type': 2,
            'enter': True,
            'style': 1,
        })
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {'text': '跟随全局', 'data': '#歌源 跟随', 'type': 2, 'enter': True, 'style': 2},
        {'text': '说明', 'data': '#歌源', 'type': 2, 'enter': True, 'style': 2},
    ])
    return rows
