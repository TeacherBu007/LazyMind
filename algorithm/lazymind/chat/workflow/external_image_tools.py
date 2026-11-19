"""Model-free media operations for revision-bound external Workflow steps."""
from __future__ import annotations

import base64
import binascii
import io
import warnings

from PIL import Image, UnidentifiedImageError

from .external_ppt_tools import ExternalToolError, _file

MAX_INPUT_BYTES = 24 * 1024 * 1024
MAX_TOTAL_PIXELS = 32 * 1024 * 1024
MAX_FRAMES = 120


def meme_add_caption(
    content_base64: str,
    caption: str,
    caption_box: list[float] | None = None,
    text_color: str = '#FFFFFF',
    stroke_color: str = '#000000',
    stroke_width_ratio: float = 0.08,
) -> dict:
    """Add an exact caption to supplied image/GIF bytes with the native renderer.

    Read the actual generated/bound image on the Agent host and supply its bytes
    as content_base64. Server paths, URLs and font paths are not accepted. For
    large inputs use workflow.tool.call arguments_file and result_directory.
    caption_box is [left, top, right, bottom] normalized to 0..1; omit it for
    the native lower caption area. Existing dimensions and GIF timing are kept.
    Publish the returned file as meme_static_output (static) or gif_output
    (animated), preserving the native step contract and output order.
    """
    if len(content_base64) > MAX_INPUT_BYTES * 4 // 3 + 4:
        raise ExternalToolError('INVALID_ARGUMENT', 'Image exceeds the 24 MiB transfer limit.', public=True)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ExternalToolError('INVALID_ARGUMENT', 'content_base64 must contain encoded image bytes.', public=True) from exc
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ExternalToolError('INVALID_ARGUMENT', 'Provide a nonempty image within 24 MiB.', public=True)
    if not caption.strip() or len(caption) > 1000:
        raise ExternalToolError('INVALID_ARGUMENT', 'caption must contain 1–1000 characters.', public=True)

    from lazymind.chat.engine.tools.infra.image_workflow_support import (
        _render_caption_frame,
    )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                count = getattr(source, 'n_frames', 1)
                if count > MAX_FRAMES or source.width * source.height * count > MAX_TOTAL_PIXELS:
                    raise ExternalToolError('INVALID_ARGUMENT', 'Image dimensions or frame count exceed the media limit.', public=True)
                if source.format not in {'PNG', 'JPEG', 'WEBP', 'GIF'}:
                    raise ExternalToolError('INVALID_ARGUMENT', 'Use PNG, JPEG, WebP or GIF image bytes.', public=True)
                frames, durations = [], []
                layout = None
                loop = int(source.info.get('loop') or 0)
                for index in range(count):
                    source.seek(index)
                    frame, current = _render_caption_frame(source.copy(), caption, caption_box,
                                                           text_color, stroke_color, stroke_width_ratio)
                    layout = layout or current
                    frames.append(frame.convert('P', palette=Image.Palette.ADAPTIVE) if count > 1 else frame)
                    durations.append(int(source.info.get('duration') or 100))
                output = io.BytesIO()
                if count > 1:
                    frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:],
                                   duration=durations, loop=loop, disposal=2, optimize=False)
                else:
                    frames[0].save(output, format='PNG')
                width, height = source.size
    except ExternalToolError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise ExternalToolError('INVALID_ARGUMENT', 'Check image bytes, caption box, colors and stroke ratio.', public=True) from exc
    except RuntimeError as exc:
        raise ExternalToolError('DEPENDENCY_MISSING', 'Install a font supporting the caption characters in LazyMind, then retry.') from exc
    animated = count > 1
    return {'success': True, 'caption': caption, 'animated': animated, 'width': width, 'height': height,
            'font_size': layout['font_size'], 'caption_box_px': list(layout['caption_box_px']),
            'text_bbox_px': list(layout['text_bbox_px']), 'text_color': text_color,
            'stroke_color': stroke_color, 'stroke_width_ratio': stroke_width_ratio,
            'files': [_file(output.getvalue(), 'captioned.gif' if animated else 'captioned.png',
                             'image/gif' if animated else 'image/png')]}
