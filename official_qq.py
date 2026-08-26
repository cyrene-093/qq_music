"""直连 QQ 音乐官方搜索；播放直链不可用时由 sources 层走备用源。"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

log = logging.getLogger('plugins.qq_music.official')

_MUSICU = 'https://u.y.qq.com/cgi-bin/musicu.fcg'
_SEARCH_H5 = 'https://c.y.qq.com/soso/fcgi-bin/client_search_cp'
_SMARTBOX = 'https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg'
_COVER = 'https://y.gtimg.cn/music/photo_new/T002R300x300M000{mid}.jpg'
_GUID = str(random.randint(1_000_000_000, 1_999_999_999))
_PLAY_RESTRICTED = False


def play_restricted() -> bool:
    return _PLAY_RESTRICTED


def set_play_restricted(flag: bool) -> None:
    global _PLAY_RESTRICTED
    _PLAY_RESTRICTED = bool(flag)

_UA_H5 = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
)
_UA_WEB = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
)

_QUALITIES: tuple[tuple[str, str, str], ...] = (
    ('M800', '.mp3', '320kbps'),
    ('M500', '.mp3', '128kbps'),
    ('C400', '.m4a', 'm4a'),
)


def parse_cookie(raw: str) -> str:
    text = (raw or '').replace('\r', ' ').replace('\n', ' ').strip()
    if not text:
        return ''
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in text.split(';'):
        item = chunk.strip()
        if not item or '=' not in item:
            continue
        key, _, val = item.partition('=')
        key = key.strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        parts.append(f'{key}={val.strip()}')
    return '; '.join(parts)


def _cookie_map(raw: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for chunk in parse_cookie(raw).split(';'):
        key, _, val = chunk.partition('=')
        k = key.strip().lower()
        if k:
            found[k] = val.strip()
    return found


def _digits_uin(raw: str) -> str:
    digits = re.sub(r'\D', '', raw or '')
    return digits.lstrip('0') or digits


def cookie_uin(raw: str) -> str:
    found = _cookie_map(raw)
    if str(found.get('login_type') or '') == '2':
        uin = found.get('wxuin') or found.get('uin') or found.get('p_uin') or ''
    else:
        uin = found.get('uin') or found.get('qqmusic_uin') or found.get('wxuin') or found.get('p_uin') or ''
    return _digits_uin(uin) or '0'


def cookie_music_key(raw: str) -> str:
    found = _cookie_map(raw)
    for key in ('qm_keyst', 'qqmusic_key', 'music_key', 'wxskey'):
        val = found.get(key) or ''
        if val:
            return val
    return ''


def cookie_mask(raw: str) -> str:
    uin = cookie_uin(raw)
    if not uin or uin == '0':
        return ''
    tail = uin[-4:] if len(uin) >= 4 else uin
    return f'****{tail}'


def _headers(cookie: str = '', *, web: bool = False) -> dict[str, str]:
    h = {
        'User-Agent': _UA_WEB if web else _UA_H5,
        'Referer': 'https://y.qq.com/',
        'Origin': 'https://y.qq.com',
        'Accept': 'application/json, text/plain, */*',
    }
    blob = parse_cookie(cookie)
    if blob:
        h['Cookie'] = blob
    return h


async def _http():
    from plugins.qq_music.service import _http as _svc_http

    return await _svc_http()


async def _get_json(url: str, *, cookie: str = '', params: dict[str, Any] | None = None) -> Any:
    c = await _http()
    resp = await c.get(url, params=params, headers=_headers(cookie))
    try:
        return resp.json()
    except Exception:
        text = (resp.content or b'').decode('utf-8', errors='replace').strip()
        if text.startswith('callback'):
            text = text[text.find('(') + 1:text.rfind(')')]
            try:
                return json.loads(text)
            except Exception:
                return None
        return None


async def _post_json(payload: dict[str, Any], *, cookie: str = '') -> Any:
    c = await _http()
    headers = _headers(cookie, web=True)
    headers['Content-Type'] = 'application/json;charset=utf-8'
    resp = await c.post(_MUSICU, json=payload, headers=headers)
    try:
        return resp.json()
    except Exception:
        return None


def _album_cover(album_mid: str) -> str:
    mid = (album_mid or '').strip()
    return _COVER.format(mid=mid) if mid else ''


def _norm_item(item: dict[str, Any]) -> dict[str, Any]:
    album = item.get('album') if isinstance(item.get('album'), dict) else {}
    file_info = item.get('file') if isinstance(item.get('file'), dict) else {}
    mid = str(item.get('mid') or item.get('songmid') or '').strip()
    album_mid = str(
        album.get('mid') or item.get('albummid') or item.get('album_mid') or ''
    ).strip()
    media_mid = str(
        file_info.get('media_mid')
        or item.get('media_mid')
        or item.get('strMediaMid')
        or ''
    ).strip()
    cover = str(item.get('pic') or item.get('cover') or '').strip() or _album_cover(album_mid)
    singer = item.get('singer')
    return {
        'song': str(item.get('name') or item.get('title') or item.get('songname') or '未知'),
        'singer': singer if singer is not None else '',
        'album_name': str(
            album.get('name') or album.get('title') or item.get('albumname') or item.get('album_name') or ''
        ),
        'album_mid': album_mid,
        'cover': cover,
        'mid': mid,
        'media_mid': media_mid,
        'id': str(item.get('id') or item.get('songid') or item.get('songID') or ''),
        'interval': item.get('interval') or item.get('songTimeMinutes') or '',
    }


async def _search_h5(keyword: str, limit: int, cookie: str) -> list[dict[str, Any]]:
    body = await _get_json(
        _SEARCH_H5,
        cookie=cookie,
        params={
            'g_tk': 5381,
            'uin': 0,
            'format': 'json',
            'inCharset': 'utf-8',
            'outCharset': 'utf-8',
            'notice': 0,
            'platform': 'h5',
            'needNewCode': 1,
            'w': keyword,
            'zhidaqu': 1,
            'catZhida': 1,
            't': 0,
            'flag': 1,
            'ie': 'utf-8',
            'sem': 1,
            'aggr': 0,
            'perpage': limit,
            'n': limit,
            'p': 1,
            'remoteplace': 'txt.mqq.all',
        },
    )
    lst = (((body or {}).get('data') or {}).get('song') or {}).get('list') or []
    if not isinstance(lst, list):
        return []
    return [x for x in lst if isinstance(x, dict)]


async def _search_smartbox(keyword: str, limit: int, cookie: str) -> list[dict[str, Any]]:
    body = await _get_json(
        _SMARTBOX,
        cookie=cookie,
        params={'key': keyword, 'format': 'json', 'inCharset': 'utf8', 'outCharset': 'utf-8'},
    )
    lst = (((body or {}).get('data') or {}).get('song') or {}).get('itemlist') or []
    if not isinstance(lst, list):
        return []
    return [x for x in lst if isinstance(x, dict)][:limit]


async def _search_musicu(keyword: str, limit: int, cookie: str) -> list[dict[str, Any]]:
    uin = cookie_uin(cookie)
    payload = {
        'comm': {'ct': 24, 'cv': 0, 'uin': uin, 'format': 'json', 'platform': 'yqq.json'},
        'req_0': {
            'method': 'DoSearchForQQMusicDesktop',
            'module': 'music.search.SearchCgiService',
            'param': {
                'remoteplace': 'txt.yqq.top',
                'search_type': 0,
                'query': keyword,
                'page_num': 1,
                'num_per_page': limit,
            },
        },
    }
    body = await _post_json(payload, cookie=cookie)
    lst = (
        ((((body or {}).get('req_0') or {}).get('data') or {}).get('body') or {}).get('song') or {}
    ).get('list') or []
    if not isinstance(lst, list):
        return []
    return [x for x in lst if isinstance(x, dict)]


async def search_official(keyword: str, limit: int = 10, cookie: str = '') -> list[dict[str, Any]]:
    kw = (keyword or '').strip()
    if not kw:
        return []
    rows: list[dict[str, Any]] = []
    for fn in (_search_h5, _search_musicu, _search_smartbox):
        try:
            rows = await fn(kw, limit, cookie)
        except Exception:
            log.debug('官方搜索失败 impl=%s', fn.__name__, exc_info=True)
            rows = []
        if rows:
            break
    out: list[dict[str, Any]] = []
    for item in rows[:limit]:
        mapped = _norm_item(item)
        if mapped.get('mid') or mapped.get('id') or mapped.get('song'):
            out.append(mapped)
    return out


def _join_url(sip: str, purl: str) -> str:
    purl = (purl or '').strip()
    if not purl:
        return ''
    if purl.startswith('http://') or purl.startswith('https://'):
        url = purl
    else:
        host = (sip or '').strip() or 'https://dl.stream.qqmusic.qq.com/'
        url = f'{host}{purl}'
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    return url


def _quality_label(filename: str) -> str:
    name = (filename or '')[:4]
    for prefix, _, label in _QUALITIES:
        if name == prefix:
            return label
    return name or 'standard'


def _extract_play(body: Any) -> tuple[str, str, int]:
    def walk(node: Any) -> tuple[str, str, int] | None:
        if not isinstance(node, dict):
            return None
        info_list = node.get('midurlinfo')
        if isinstance(info_list, list) and info_list:
            sip = ''
            sips = node.get('sip')
            if isinstance(sips, list) and sips:
                sip = str(sips[0] or '')
            result_code = 0
            for raw in info_list:
                if not isinstance(raw, dict):
                    continue
                try:
                    result_code = int(raw.get('result') or 0)
                except (TypeError, ValueError):
                    result_code = 0
                purl = str(raw.get('purl') or raw.get('wifiurl') or '')
                url = _join_url(sip, purl)
                if url:
                    return url, str(raw.get('filename') or ''), result_code
            return '', '', result_code
        for val in node.values():
            if isinstance(val, dict):
                hit = walk(val)
                if hit:
                    return hit
        return None

    hit = walk(body or {})
    return hit or ('', '', 0)


def _play_filenames(songmid: str, media_mid: str) -> list[str]:
    ids: list[str] = []
    for mid in (media_mid, songmid):
        val = (mid or '').strip()
        if val and val not in ids:
            ids.append(val)
    names: list[str] = []
    for file_id in ids:
        for prefix, ext, _label in _QUALITIES:
            names.append(f'{prefix}{file_id}{ext}')
    return names


def _auth_comm(cookie: str) -> dict[str, Any]:
    uin = cookie_uin(cookie)
    music_key = cookie_music_key(cookie)
    comm: dict[str, Any] = {
        'uin': uin,
        'format': 'json',
        'ct': 19 if music_key else 24,
        'cv': 0,
    }
    if music_key:
        comm['authst'] = music_key
    return comm


async def _fetch_media_mid(songmid: str, cookie: str) -> str:
    payload = {
        'comm': {'ct': 24, 'cv': 0, 'uin': cookie_uin(cookie)},
        'songinfo': {
            'module': 'music.pf_song_detail_svr',
            'method': 'get_song_detail_yqq',
            'param': {'song_mid': songmid, 'song_type': 0},
        },
    }
    body = await _post_json(payload, cookie=cookie)
    track = ((((body or {}).get('songinfo') or {}).get('data') or {}).get('track_info') or {})
    file_info = track.get('file') if isinstance(track, dict) else {}
    if isinstance(file_info, dict):
        return str(file_info.get('media_mid') or '').strip()
    return ''


async def _get_vkey(mid: str, media_mid: str, cookie: str) -> tuple[str, str, int]:
    uin = cookie_uin(cookie)
    filenames = _play_filenames(mid, media_mid)
    if not filenames:
        return '', '', 0
    param = {
        'guid': _GUID,
        'songmid': [mid] * len(filenames),
        'songtype': [0] * len(filenames),
        'uin': str(uin),
        'loginflag': 1 if cookie and uin != '0' else 0,
        'platform': '20',
        'filename': filenames,
    }
    payload = {
        'comm': _auth_comm(cookie),
        'req_0': {
            'module': 'vkey.GetVkeyServer',
            'method': 'CgiGetVkey',
            'param': param,
        },
    }
    body = await _post_json(payload, cookie=cookie)
    code = ((body or {}).get('req_0') or {}).get('code')
    url, filename, result = _extract_play(body)
    log.info(
        '官方换链 mid=%s media_mid=%s ticket=%s code=%s result=%s hit=%s',
        mid,
        bool(media_mid),
        bool(cookie_music_key(cookie)),
        code,
        result,
        bool(url),
    )
    return url, filename, result


async def play_official(item: dict[str, Any], *, cookie: str = '') -> dict[str, Any] | None:
    mid = str(item.get('mid') or '').strip()
    if not mid:
        return None
    if _PLAY_RESTRICTED:
        return {
            'song': str(item.get('song') or '未知'),
            'singer': item.get('singer') or '',
            'album_name': str(item.get('album_name') or ''),
            'album_mid': str(item.get('album_mid') or ''),
            'cover': str(item.get('cover') or '') or _album_cover(str(item.get('album_mid') or '')),
            'music': '',
            'mid': mid,
            'media_mid': str(item.get('media_mid') or ''),
            'id': str(item.get('id') or ''),
            'quality': '',
            '_qq_result': 104003,
        }
    media_mid = str(item.get('media_mid') or '').strip()
    if not media_mid:
        try:
            media_mid = await _fetch_media_mid(mid, cookie)
        except Exception:
            log.debug('补 media_mid 失败 mid=%s', mid, exc_info=True)
            media_mid = ''
    try:
        url, filename, qq_result = await _get_vkey(mid, media_mid, cookie)
    except Exception:
        log.debug('官方换链失败 mid=%s', mid, exc_info=True)
        url, filename, qq_result = '', '', 0
    if qq_result == 104003:
        set_play_restricted(True)
    return {
        'song': str(item.get('song') or '未知'),
        'singer': item.get('singer') or '',
        'album_name': str(item.get('album_name') or ''),
        'album_mid': str(item.get('album_mid') or ''),
        'cover': str(item.get('cover') or '') or _album_cover(str(item.get('album_mid') or '')),
        'music': url,
        'mid': mid,
        'media_mid': media_mid,
        'id': str(item.get('id') or ''),
        'quality': _quality_label(filename) if url else '',
        '_qq_result': qq_result,
    }


async def probe_official(cookie: str = '') -> dict[str, Any]:
    """检测搜索/换链是否可用，不返回音频地址。"""
    ticket = bool(cookie_music_key(cookie))
    songs = await search_official('稻香', 1, cookie)
    if not songs:
        return {
            'search_ok': False,
            'play_ok': False,
            'fallback_ok': False,
            'quality': '',
            'song': '',
            'has_ticket': ticket,
            'qq_result': 0,
            'hint': 'no_search',
        }
    data = await play_official(songs[0], cookie=cookie)
    ok = bool(data and str(data.get('music') or '').strip())
    qq_result = 0
    try:
        qq_result = int((data or {}).get('_qq_result') or 0)
    except (TypeError, ValueError):
        qq_result = 0
    fallback_ok = False
    trial = False
    fb_quality = ''
    if not ok:
        from plugins.qq_music.sources import get_source, play_source

        kw = str(songs[0].get('song') or '稻香')
        try:
            got = await play_source(
                get_source('official_qq'), songs[0], keyword=kw, index=1, _fallback=True,
            )
        except Exception:
            got = None
        if got and str(got.get('music') or '').strip():
            fallback_ok = True
            trial = bool(got.get('_trial'))
            fb_quality = str(got.get('quality') or '')
    if ok:
        hint = 'ok'
    elif qq_result == 104003 and fallback_ok and not trial:
        hint = 'restricted_full'
    elif qq_result == 104003 and fallback_ok and trial:
        hint = 'restricted_trial'
    elif qq_result == 104003:
        hint = 'restricted'
    elif not ticket:
        hint = 'no_ticket'
    else:
        hint = 'empty_purl'
    return {
        'search_ok': True,
        'play_ok': ok,
        'fallback_ok': fallback_ok or ok,
        'trial': trial,
        'quality': str((data or {}).get('quality') or fb_quality),
        'song': str((data or songs[0]).get('song') or ''),
        'has_ticket': ticket,
        'qq_result': qq_result,
        'hint': hint,
    }
