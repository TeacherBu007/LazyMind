"""Actual caption rendering through the pinned external tool boundary."""
import base64
import importlib.util
import inspect
import io
import sys
from pathlib import Path

import pytest
from lazymind.chat.engine.tools.infra import image_workflow_support as native
from lazymind.chat.workflow import external_image_tools as media
from lazymind.chat.workflow.external_tools import (
    ExternalToolCallRequest,
    ExternalToolRequest,
    call_external_tool,
    list_external_tools,
    package_tree_hash,
)
from PIL import Image, ImageFont

ROOT = Path(__file__).resolve().parents[3]


def request(step='enhance_image', replace=None):
    root = ROOT / 'workflows/image-workflow'
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    files.update(replace or {})
    digest = package_tree_hash(files)
    return {'workflow_id': 'image-workflow', 'revision_id': 'test-fixed-revision', 'step_id': step,
            'tree_hash': digest, 'package': {'tree_hash': digest, 'files': {
                p: base64.b64encode(v).decode() for p, v in files.items()}}}


@pytest.fixture
def caption_font(tmp_path, monkeypatch):
    font = ImageFont.load_default(size=16)
    path = tmp_path / 'font.ttf'
    path.write_bytes(font.path.getvalue())
    monkeypatch.setattr(native, '_LATIN_FONT_CANDIDATES', (str(path),))
    return path


def image_bytes(format='PNG'):
    output = io.BytesIO()
    image = Image.new('RGB', (320, 240), '#BBD7E8')
    if format == 'GIF':
        image.save(output, format='GIF', save_all=True,
                   append_images=[Image.new('RGB', image.size, '#E8D7BB')], duration=[80, 120], loop=2)
    else:
        image.save(output, format=format)
    return output.getvalue()


def call(arguments, **kwargs):
    return call_external_tool(ExternalToolCallRequest(**request(**kwargs), tool_name='meme_add_caption',
                                                     arguments=arguments))['result']


def test_original_package_exposes_caption_only_in_postprocessing_step():
    tools = list_external_tools(ExternalToolRequest(**request()))['tools']
    caption = next(t for t in tools if t['name'] == 'meme_add_caption')
    assert set(caption['input_schema']['required']) == {'content_base64', 'caption'}
    assert 'image_url' not in caption['input_schema']['properties']
    assert 'font_path' not in caption['input_schema']['properties']
    assert list_external_tools(ExternalToolRequest(**request(step='analyze_subject')))['tools'] == []


def test_static_caption_uses_native_layout_and_changes_only_its_box(caption_font, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *args, **kwargs: pytest.fail('Captioning must not fetch URLs'))
    raw = image_bytes()
    result = call({'content_base64': base64.b64encode(raw).decode(), 'caption': 'RECEIVED'})
    assert result['caption'] == 'RECEIVED' and result['animated'] is False
    file = result['files'][0]
    assert file['mime_type'] == 'image/png'
    output = Image.open(io.BytesIO(base64.b64decode(file['content_base64']))).convert('RGB')
    original = Image.open(io.BytesIO(raw)).convert('RGB')
    assert output.size == original.size == (320, 240)
    box = tuple(result['caption_box_px'])
    assert output.crop(box).tobytes() != original.crop(box).tobytes()
    assert output.crop((0, 0, 320, box[1])).tobytes() == original.crop((0, 0, 320, box[1])).tobytes()
    assert not {'local_path', 'image_url', 'font_path'} & result.keys()


def test_gif_caption_preserves_frames_timing_and_loop(caption_font):
    result = call({'content_base64': base64.b64encode(image_bytes('GIF')).decode(), 'caption': 'OK'})
    assert result['animated'] is True
    output = Image.open(io.BytesIO(base64.b64decode(result['files'][0]['content_base64'])))
    assert output.n_frames == 2 and output.info['loop'] == 2
    durations = []
    for index in range(output.n_frames):
        output.seek(index)
        durations.append(output.info['duration'])
    assert durations == [80, 120]


@pytest.mark.parametrize('arguments', [
    {'image_url': '/etc/passwd', 'caption': 'OK'},
    {'content_base64': 'bad!', 'caption': 'OK'},
    {'content_base64': base64.b64encode(b'not an image').decode(), 'caption': 'OK'},
    {'content_base64': base64.b64encode(image_bytes()).decode(), 'caption': ''},
    {'content_base64': base64.b64encode(image_bytes()).decode(), 'caption': 'OK', 'font_path': '/etc/passwd'},
    {'content_base64': base64.b64encode(image_bytes()).decode(), 'caption': 'OK', 'caption_box': [0.8, 0.8, 0.2, 0.2]},
])
def test_rejects_invalid_parameters_and_server_path_access(arguments, caption_font):
    with pytest.raises(media.ExternalToolError) as raised:
        call(arguments)
    assert raised.value.code == 'INVALID_ARGUMENT'


def test_modified_binding_is_not_executed():
    with pytest.raises(media.ExternalToolError) as raised:
        list_external_tools(ExternalToolRequest(**request(replace={
            'scripts/external_image_tools.py': b'raise AssertionError("not audited")'})))
    assert raised.value.code == 'TOOL_NOT_ALLOWED'


def test_native_entry_point_keeps_its_original_image_url_interface():
    path = ROOT / 'workflows/image-workflow/scripts/external_image_tools.py'
    spec = importlib.util.spec_from_file_location('native_caption_entry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.meme_add_caption is native.meme_add_caption
    assert 'image_url' in inspect.signature(module.meme_add_caption).parameters


@pytest.mark.skipif(sys.platform != 'darwin', reason='Requires the installed macOS CJK fonts')
def test_chinese_caption_renders_real_pixels():
    result = call({'content_base64': base64.b64encode(image_bytes()).decode(), 'caption': '收到'})
    assert result['caption'] == '收到'
    assert result['font_size'] > 0
    assert len(base64.b64decode(result['files'][0]['content_base64'])) > 100
