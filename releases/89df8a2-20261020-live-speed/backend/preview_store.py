"""Immutable saved previews outlive RAM-cache eviction and backend restarts."""
import hashlib
import json
import os
import pathlib
import re


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) is not None


class PreviewStore:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.index = self.root / '.documents'

    def save_group(self, group_id, job_ids, seed, document):
        manifest = dict(version=1, document=bool(document), document_id=group_id,
                        page_count=len(job_ids), job_ids=job_ids, seed=seed)
        self.index.mkdir(exist_ok=True)
        path = self.index / (group_id + '.json')
        temp = path.with_suffix('.tmp')
        # Publish only after all pages have been saved; a partially written
        # index is never advertised to the browser.
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        return manifest

    def group(self, group_id):
        if not valid_id(group_id):
            raise ValueError('文档编号无效')
        path = self.index / (group_id + '.json')
        try:
            if path.stat().st_size > 65536:
                raise ValueError('文档索引过大')
            manifest = json.loads(path.read_text(encoding='utf8'))
            ids = manifest['job_ids']
            if (type(manifest.get('version')) is not int or manifest['version'] != 1 or type(manifest.get('document')) is not bool
                    or manifest.get('document_id') != group_id or not isinstance(ids, list)
                    or not 1 <= len(ids) <= 100 or any(not valid_id(job) for job in ids)
                    or len(set(ids)) != len(ids) or type(manifest.get('page_count')) is not int
                    or manifest['page_count'] != len(ids)):
                raise ValueError('文档索引无效')
            return manifest
        except FileNotFoundError as error:
            raise ValueError('找不到已保存的文档索引；旧预览的各页仍可继续写入或单独下载') from error
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            raise ValueError('已保存的文档索引损坏，请重新生成') from error

    def job(self, job_id):
        if not valid_id(job_id):
            raise ValueError('任务编号无效')
        folder = self.root / job_id
        if folder.resolve().parent != self.root:
            raise ValueError('预览文件路径无效')
        try:
            def read(name, limit):
                path = folder / name
                if path.stat().st_size > limit:
                    raise ValueError('预览文件过大')
                return path.read_bytes()
            gcode = read('job.gcode', 32 * 1024 * 1024)
            metadata = json.loads(read('metadata.json', 64 * 1024 * 1024).decode('utf8'))
            svg = read('preview.svg', 128 * 1024 * 1024).decode('utf8').replace('\r\n', '\n')
            if (not isinstance(metadata, dict) or metadata.get('sha256') != hashlib.sha256(gcode).hexdigest()
                    or type(metadata.get('commands')) is not int or metadata['commands'] != len(gcode.splitlines())
                    or type(metadata.get('points')) is not int or not 0 <= metadata['points'] <= 200000
                    or ('preview_group_id' in metadata and not valid_id(metadata['preview_group_id']))):
                raise ValueError('预览校验不匹配')
            return folder, dict(metadata=metadata, gcode=gcode.decode('ascii'), svg=svg)
        except FileNotFoundError as error:
            raise ValueError('找不到已保存的预览文件，请重新生成') from error
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            raise ValueError('已保存的预览文件不完整或已损坏，请重新生成') from error
