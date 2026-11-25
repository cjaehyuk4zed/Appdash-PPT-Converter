import io
import math
import zipfile

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from converter.engine import conversion, inspect_file, page_indices
from converter.models import Options
from converter.presentation import preserve_pptx, validate_presentation
from tests.conftest import convert, item


@pytest.mark.parametrize('fmt', ['jpeg', 'png', 'webp', 'tiff', 'bmp', 'avif'])
def test_image_output_formats_and_bounds(tmp_path, noise_path, fmt):
    result, directory = convert(tmp_path, [item(noise_path)], format=fmt, max_width=160)
    output = result['outputs'][0]
    assert output['name'].rsplit('.', 1)[0] == 'photo_001'
    with Image.open(directory / output['name']) as image:
        assert image.size == (160, 120)
    assert (directory / 'preview.png').is_file()
    with zipfile.ZipFile(directory / 'results.zip') as archive:
        assert archive.namelist() == [output['name']]


@pytest.mark.parametrize('fmt', ['JPEG', 'PNG', 'WEBP', 'TIFF', 'BMP', 'GIF', 'AVIF', 'HEIF'])
def test_image_inputs(tmp_path, image_path, fmt):
    path = tmp_path / 'input.bin'
    with Image.open(image_path) as source:
        source.save(path, format=fmt)
    info = inspect_file({'path': str(path), 'thumbnail': str(tmp_path / 'thumb.png'), 'pixel_limit': 40000000, 'page_limit': 500})
    assert info['kind'] == 'image' and info['width'] == 640


def test_size_target_checks_final_image_bytes(tmp_path, noise_path):
    result, directory = convert(tmp_path, [item(noise_path)], mode='target', target_kb=25, quality=95, min_quality=1, min_long_edge=64)
    output = result['outputs'][0]
    assert output['target_met'] and output['size'] <= 25000
    assert output['size'] == (directory / output['name']).stat().st_size


def test_unreachable_target_returns_smallest_with_warning(tmp_path, noise_path):
    result, _ = convert(tmp_path, [item(noise_path)], mode='target', target_kb=.001, quality=95, min_quality=1, allow_downscale=False)
    assert result['outputs'][0]['target_met'] is False
    assert result['warnings']


def test_exif_rotation_and_stripping(tmp_path):
    path = tmp_path / 'phone.jpg'
    exif = Image.Exif()
    exif[274], exif[270] = 6, 'private caption'
    Image.new('RGB', (120, 80), 'red').save(path, exif=exif)
    result, directory = convert(tmp_path, [item(path)])
    with Image.open(directory / result['outputs'][0]['name']) as output:
        assert output.size == (80, 120) and not output.getexif()


def test_images_to_pptx_order_margins_background_and_metadata(tmp_path, image_path):
    blue = tmp_path / 'blue.png'
    Image.new('RGBA', (480, 640), (0, 0, 255, 180)).save(blue)
    result, directory = convert(tmp_path, [item(blue), item(image_path)], operation='images_to_ppt', paper='wide', margin_mm=10, background='#fafafa')
    output = result['outputs'][0]
    deck = Presentation(str(directory / output['name']))
    assert len(deck.slides) == 2
    assert deck.slide_width / deck.slide_height == pytest.approx(16 / 9, rel=1e-5)
    assert deck.slides[0].shapes[0].left > deck.slides[1].shapes[0].left
    assert deck.core_properties.author == ''
    assert result['warnings']
    with Image.open(directory / 'preview.png') as preview:
        assert preview.width / preview.height == pytest.approx(16 / 9, rel=.001)
        assert preview.getpixel((0, 0)) == (250, 250, 250)


def test_pptx_size_search_uses_whole_file_and_keeps_slide_dimensions(tmp_path, noise_path):
    result, directory = convert(tmp_path, [item(noise_path), item(noise_path)], operation='images_to_ppt', mode='target', target_kb=48, page_image_kb=12, quality=95, min_quality=1, min_long_edge=64)
    output = result['outputs'][0]
    deck = Presentation(str(directory / output['name']))
    assert len(deck.slides) == 2 and output['size'] <= 48000 and output['target_met']
    assert output['page_image_target_met']
    assert deck.slide_width.inches == pytest.approx(640 / 200)
    assert all(len(slide.shapes[0].image.blob) <= 12000 for slide in deck.slides)


def test_original_jpeg_embedding_removes_exif_without_reencoding(tmp_path, image_path):
    path = tmp_path / 'original.jpg'
    exif = Image.Exif()
    exif[270] = 'private caption'
    with Image.open(image_path) as image:
        image.save(path, quality=87, exif=exif)
    result, directory = convert(tmp_path, [item(path)], operation='images_to_ppt')
    output = result['outputs'][0]
    deck = Presentation(str(directory / output['name']))
    embedded = deck.slides[0].shapes[0].image.blob
    with Image.open(io.BytesIO(embedded)) as decoded, Image.open(path) as original:
        assert not decoded.getexif()
        assert decoded.tobytes() == original.tobytes()
    assert output['original_pages'] == 1 and output['quality'] is None


def test_split_and_preview_are_separate_single_slide_pptx(tmp_path, image_path):
    for split, preview, count in [(True, False, 2), (False, True, 1)]:
        directory = tmp_path / f'{split}-{preview}'
        directory.mkdir()
        result = conversion({'directory': str(directory), 'items': [item(image_path)] * 2, 'options': {'operation': 'images_to_ppt', 'split': split}, 'preview': preview, 'pixel_limit': 40000000}, lambda _: None)
        assert len(result['outputs']) == count
        assert len({output['name'] for output in result['outputs']}) == count
        assert [output['name'] for output in result['outputs']] == [f'sample_{index + 1:03d}.pptx' for index in range(count)]
        assert all(len(Presentation(str(directory / output['name'])).slides) == 1 for output in result['outputs'])


def test_suffix_names_keep_unicode_dots_and_avoid_duplicate_stem_collisions(tmp_path):
    first = tmp_path / '자료.v1.png'
    second = tmp_path / '자료.v1.jpg'
    Image.new('RGB', (100, 80), 'red').save(first)
    Image.new('RGB', (100, 80), 'blue').save(second)
    result, directory = convert(tmp_path, [item(second), item(first), item(second)], format='png')
    expected = ['자료.v1_001.png', '자료.v1_002.png', '자료.v1_003.png']
    assert [output['name'] for output in result['outputs']] == expected
    with zipfile.ZipFile(directory / 'results.zip') as archive:
        assert archive.namelist() == expected
        for name, channel in zip(expected, [2, 0, 2], strict=True):
            with Image.open(io.BytesIO(archive.read(name))) as image:
                assert image.convert('RGB').getpixel((5, 5))[channel] > 200


def test_preserve_content_reorders_slides_and_keeps_editable_text(tmp_path):
    source = tmp_path / 'source.pptx'
    deck = Presentation()
    for name in ['one', 'two', 'three']:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = name
    deck.core_properties.author = 'Private'
    deck.save(str(source))
    items = [{'pptx_path': str(source), 'page': page, 'rotation': 0} for page in [2, 0]]
    output = tmp_path / 'output.pptx'
    preserve_pptx(items, Options(operation='ppt_compress'), output)
    result = Presentation(str(output))
    assert [slide.shapes[0].text for slide in result.slides] == ['three', 'one']
    assert result.core_properties.author == ''
    items[0]['rotation'] = 90
    with pytest.raises(ValueError, match='cannot rotate'):
        preserve_pptx(items, Options(operation='ppt_compress'), output)


def test_page_ranges_and_request_validation():
    assert page_indices('3, 1-2, 2', 3) == [2, 0, 1]
    for value in ['0', '2-1', '4', '1,,2', 'x']:
        with pytest.raises(ValueError):
            page_indices(value, 3)
    for settings in [{'quality': True}, {'target_kb': math.nan}, {'extra': 1}, {'mode': 'target'}, {'quality': 40, 'mode': 'target', 'target_kb': 20}, {'operation': 'ppt_to_images', 'dpi': 72, 'mode': 'target', 'target_kb': 20}, {'operation': 'pdf_to_images'}]:
        with pytest.raises(ValueError):
            Options.model_validate({'operation': 'image_convert', **settings})


def test_corrupt_and_macro_packages_rejected(tmp_path):
    path = tmp_path / 'bad.pptx'
    path.write_bytes(b'not an office document')
    with pytest.raises(ValueError, match='Cannot open'):
        validate_presentation(path, '.pptx', 500)
    with zipfile.ZipFile(path, 'w') as archive:
        for name in ['ppt/presentation.xml', '[Content_Types].xml', 'ppt/vbaProject.bin']:
            archive.writestr(name, b'')
    with pytest.raises(ValueError, match='Macro'):
        validate_presentation(path, '.pptx', 500)


def test_output_safety_limit(tmp_path, noise_path):
    directory = tmp_path / 'result'
    directory.mkdir()
    with pytest.raises(ValueError, match='output safety limit'):
        conversion({'directory': str(directory), 'items': [item(noise_path)], 'options': {'operation': 'image_convert', 'format': 'bmp'}, 'pixel_limit': 40000000, 'output_limit': 1000}, lambda _: None)
