"""Exercise real Office rendering and every slide conversion through HTTP workers."""
import io
import os
import subprocess
import sys

import pytest
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches
from starlette.testclient import TestClient

from converter.presentation import renderer_status
from converter.server import Config, create_app
from tests.test_server import upload, wait


@pytest.mark.parametrize('suffix', ['.pptx', '.ppt'])
def test_real_slide_rendering_selection_compression_and_downloads(tmp_path, suffix):
    if not renderer_status()['available']:
        pytest.skip('Install PowerPoint or LibreOffice for the renderer integration test.')
    if suffix == '.ppt' and (os.name != 'nt' or renderer_status()['name'] != 'Microsoft PowerPoint'):
        pytest.skip('Legacy PPT fixture generation uses Windows PowerPoint.')
    source = tmp_path / 'slides.pptx'
    deck = Presentation()
    deck.core_properties.author = 'Private Author'
    for index, color in enumerate(['CC3333', '33CC33', '3333CC']):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(color)
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = f'Slide {index + 1} / 한글 렌더링'
        if index == 2:
            slide._element.set('show', '0')
    deck.save(str(source))
    if suffix == '.ppt':
        legacy = source.with_suffix('.ppt')
        # Keep COM lifetime separate from the test runner, as in the real upload worker.
        subprocess.run([sys.executable, '-c', '''
import sys
import pythoncom
import win32com.client
pythoncom.CoInitialize()
application = win32com.client.DispatchEx('PowerPoint.Application')
document = None
try:
    document = application.Presentations.Open(sys.argv[1], -1, 0, 0)
    document.SaveCopyAs(sys.argv[2], 1)
finally:
    if document is not None:
        document.Close()
    document = application = None
    pythoncom.CoUninitialize()
''', str(source), str(legacy)], check=True, capture_output=True, timeout=40)
        source = legacy
    app = create_app(Config(data_dir=tmp_path / 'runtime', allowed_hosts=['testserver'], timeout=180))
    with TestClient(app) as client:
        asset = upload(client, source)
        assert asset['pages'] == 3 and asset['kind'] == 'ppt'
        assert asset['warnings']
        assert not {'path', 'render_path', 'pptx_path'} & set(asset)
        selections = [{'file_id': asset['id'], 'page': page} for page in [2, 0]]
        original_bytes = source.read_bytes()
        cases = [
            {'operation': 'ppt_to_images', 'format': 'png', 'dpi': 72},
            {'operation': 'ppt_to_images', 'format': 'jpeg', 'mode': 'target', 'target_kb': 1000, 'quality': 95, 'min_quality': 1},
            {'operation': 'ppt_compress', 'compression': 'preserve'},
            {'operation': 'ppt_compress', 'compression': 'raster', 'dpi': 72},
        ]
        for options in cases:
            response = client.post('/api/jobs', json={'selections': selections, 'options': options})
            assert response.status_code == 202, response.text
            job = wait(client, response.json()['id'])
            assert job['state'] == 'completed', job
            outputs = [client.get(f"/api/jobs/{job['id']}/outputs/{index}").content for index in range(len(job['outputs']))]
            assert client.get(f"/api/jobs/{job['id']}/preview").content.startswith(b'\x89PNG')
            assert client.get(f"/api/jobs/{job['id']}/zip").content.startswith(b'PK')
            if options['operation'] == 'ppt_to_images':
                assert len(outputs) == 2
                with Image.open(io.BytesIO(outputs[0])) as image:
                    red, green, blue = image.convert('RGB').getpixel((5, 5))
                    assert blue > red * 2 and blue > green * 2
                if options.get('mode') == 'target':
                    assert all(output['target_met'] and output['quality'] == 95 for output in job['outputs'])
            else:
                result = Presentation(io.BytesIO(outputs[0]))
                assert len(result.slides) == 2
                if options['compression'] == 'preserve':
                    assert [slide.shapes[0].text for slide in result.slides] == ['Slide 3 / 한글 렌더링', 'Slide 1 / 한글 렌더링']
                else:
                    assert all(slide.shapes[0].shape_type == 13 for slide in result.slides)
                    assert job['warnings']
                assert result.core_properties.author == ''
            assert source.read_bytes() == original_bytes
            assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
        # A range is applied after checkbox selection; rotation is applied to output pixels.
        response = client.post('/api/jobs', json={'selections': [{'file_id': asset['id'], 'page': page, 'rotation': 90} for page in range(3)], 'options': {'operation': 'ppt_to_images', 'format': 'png', 'pages': '2-3', 'dpi': 72}, 'preview': True})
        job = wait(client, response.json()['id'])
        assert job['state'] == 'completed' and len(job['outputs']) == 1
        assert job['outputs'][0]['height'] > job['outputs'][0]['width']
        assert client.delete(f"/api/files/{asset['id']}").status_code == 200
