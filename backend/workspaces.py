"""One durable, versioned workbench per controller device, shared by all browsers."""
import copy
import hashlib
import json
import os
import pathlib
import threading


class Workspaces:
    def __init__(self, directory):
        self.root = pathlib.Path(directory) / '.workspaces'
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()

    def path(self, device):
        if not isinstance(device, str) or not device or len(device) > 200:
            raise ValueError('设备编号无效')
        return self.root / (hashlib.sha256(device.encode('utf8')).hexdigest() + '.json')

    def read(self, device):
        with self.lock:
            path = self.path(device)
            if not path.exists():
                return dict(device=device, revision=0, draft=None, document_id=None)
            try:
                result = json.loads(path.read_text(encoding='utf8'))
                if result['device'] != device or type(result['revision']) is not int:
                    raise ValueError('invalid workspace')
                return result
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError('设备界面存档损坏，请检查服务端存档') from error

    def update(self, device, data, generate=None):
        with self.lock:
            current = self.read(device)
            if type(data.get('revision')) is not int or data['revision'] != current['revision']:
                return dict(conflict=True, workspace=current)
            draft = data.get('draft')
            if not isinstance(draft, dict) or len(json.dumps(draft, allow_nan=False).encode()) > 480000:
                raise ValueError('界面内容无效或过大')
            pages = draft.get('pages')
            if (not isinstance(pages, list) or not 1 <= len(pages) <= 100
                    or any(not isinstance(p, str) for p in pages) or sum(map(len, pages)) > 30000):
                raise ValueError('文档需要 1–100 页，全文最多 30000 字')
            if draft.get('layoutMode') not in ('', 'notebook', 'grid'):
                raise ValueError('排版模式无效')
            fields, mix, modes = draft.get('fields'), draft.get('font_mix'), draft.get('modeDrafts')
            if (not isinstance(fields, dict) or any(not isinstance(v, (str, bool)) for v in fields.values())
                    or not isinstance(mix, list) or len(mix) > 16
                    or any(not isinstance(f, dict) or not isinstance(f.get('font_id'), str) for f in mix)
                    or not isinstance(modes, dict)
                    or any(k not in ('notebook', 'grid') or not isinstance(v, dict)
                           or not isinstance(v.get('values'), dict) for k, v in modes.items())):
                raise ValueError('界面设置格式无效')
            for name, maximum in (('sourceIndex', len(pages) - 1), ('outputIndex', 99)):
                value = draft.get(name, 0)
                if type(value) is not int or not 0 <= value <= maximum:
                    raise ValueError('页码无效')
            # Generation and publication are serialized with edits/reset. Failed
            # generation keeps the previous committed workbench untouched.
            document_id = current['document_id']
            if data.get('clear_preview') is True:
                document_id = None
            if generate is not None:
                document_id = generate()['document_id']
            result = dict(device=device, revision=current['revision'] + 1,
                          draft=copy.deepcopy(draft), document_id=document_id)
            path = self.path(device)
            temp = path.with_suffix('.tmp')
            with temp.open('w', encoding='utf8', newline='\n') as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            return dict(conflict=False, workspace=result)
