"""Local slide rendering and PPTX assembly. Originals are always opened read-only."""
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Mm


def libreoffice():
    configured = os.environ.get('CONVERTER_SOFFICE', '')
    for candidate in (configured, shutil.which('soffice'), r'C:\Program Files\LibreOffice\program\soffice.exe'):
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def renderer_status():
    if libreoffice():
        return {'available': True, 'name': 'LibreOffice'}
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'PowerPoint.Application\CLSID'):
                return {'available': True, 'name': 'Microsoft PowerPoint'}
        except OSError:
            pass
    return {'available': False, 'name': None}


def validate_presentation(path, suffix, page_limit):
    if suffix == '.pptx':
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 20000 or sum(entry.file_size for entry in entries) > 512_000_000:
                    raise ValueError('PPTX exceeds the expanded-file safety limit.')
                names = set(archive.namelist())
                if 'ppt/presentation.xml' not in names or '[Content_Types].xml' not in names:
                    raise ValueError('Choose a valid PPT or PPTX presentation.')
                if any(name.lower().endswith('vbaproject.bin') for name in names):
                    raise ValueError('Macro-enabled presentations are not supported. Save a macro-free PPTX first.')
            presentation = Presentation(str(path))
            if not 0 < len(presentation.slides) <= page_limit:
                raise ValueError('PPT has no slides or exceeds the slide-count limit.')
        except (zipfile.BadZipFile, KeyError, OSError) as error:
            raise ValueError('Cannot open PPTX. Password-protected or damaged files are not supported.') from error
    else:
        import olefile
        try:
            with olefile.OleFileIO(path) as document:
                if not document.exists('PowerPoint Document'):
                    raise ValueError('Choose a valid PPT or PPTX presentation.')
                if document.exists('EncryptedPackage') or document.exists('EncryptionInfo'):
                    raise ValueError('Password-protected presentations are not supported.')
                if document.exists('Current User'):
                    header = document.openstream('Current User').read(20)
                    if header[12:16] == bytes.fromhex('dfc4d1f3'):
                        raise ValueError('Password-protected presentations are not supported.')
        except OSError as error:
            raise ValueError('Cannot open PPT. Password-protected or damaged files are not supported.') from error


def powerpoint_render(source, pdf, normalized, page_limit):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    application = document = None
    old_security = old_alerts = None
    try:
        application = win32com.client.DispatchEx('PowerPoint.Application')
        old_security, old_alerts = application.AutomationSecurity, application.DisplayAlerts
        application.AutomationSecurity = 3  # Force-disable document macros.
        application.DisplayAlerts = 1
        document = application.Presentations.Open(str(source), -1, 0, 0)
        if not 0 < document.Slides.Count <= page_limit:
            raise ValueError('PPT has no slides or exceeds the slide-count limit.')
        if normalized:
            document.SaveCopyAs(str(normalized), 24)  # Open XML presentation.
        # Some Office versions ignore PrintHiddenSlides during fixed-format export.
        # Change only the disposable, read-only rendering copy; preserve the source flags.
        for slide in document.Slides:
            slide.SlideShowTransition.Hidden = 0
        slide = None
        # Named parameters ensure hidden slides remain in their original indices.
        document.ExportAsFixedFormat(str(pdf), 2, Intent=2, PrintHiddenSlides=-1, PrintRange=None)
        document.Saved = -1
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('PowerPoint could not render this file. Open it in PowerPoint and save a macro-free, unprotected PPTX, then try again.') from error
    finally:
        if document is not None:
            try:
                document.Close()
            except Exception:
                pass
        if application is not None:
            try:
                if old_security is not None:
                    application.AutomationSecurity = old_security
                if old_alerts is not None:
                    application.DisplayAlerts = old_alerts
            except Exception:
                pass
        # PowerPoint may share an application with the user. Never call Quit().
        document = application = None
        pythoncom.CoUninitialize()


def inspect_presentation(payload):
    source = Path(payload['path'])
    suffix = Path(payload.get('name', source.name)).suffix.lower()
    validate_presentation(source, suffix, payload['page_limit'])
    status = renderer_status()
    if not status['available']:
        raise ValueError('PPT rendering requires Microsoft PowerPoint on Windows or LibreOffice. Image conversion remains available.')
    render_source = source.with_name('presentation' + suffix)
    shutil.copyfile(source, render_source)
    pdf = source.with_name('presentation.pdf')
    normalized = source.with_name('normalized.pptx') if suffix == '.ppt' else None
    if status['name'] == 'LibreOffice':
        with tempfile.TemporaryDirectory(prefix='office-', dir=source.parent) as profile:
            common = [libreoffice(), '-env:UserInstallation=' + Path(profile).as_uri(), '--headless', '--nologo', '--nodefault', '--norestore']
            try:
                subprocess.run(common + ['--convert-to', 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}', '--outdir', str(source.parent), str(render_source)], check=True, capture_output=True, timeout=120)
                if normalized:
                    subprocess.run(common + ['--convert-to', 'pptx', '--outdir', str(source.parent), str(render_source)], check=True, capture_output=True, timeout=40)
                    shutil.move(str(source.with_name('presentation.pptx')), normalized)
            except (subprocess.SubprocessError, OSError) as error:
                raise ValueError('LibreOffice could not render this presentation. Try saving it as PPTX first.') from error
    else:
        powerpoint_render(render_source, pdf, normalized, payload['page_limit'])
    if not pdf.is_file():
        raise ValueError('The renderer did not produce a slide preview. Check that the presentation is unprotected.')
    pptx_path = normalized or render_source
    deck = Presentation(str(pptx_path))
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as rendered:
        if len(rendered) != len(deck.slides):
            raise ValueError('The renderer changed the slide count. Save all slides as a new PPTX and try again.')
    if not 0 < len(deck.slides) <= payload['page_limit']:
        raise ValueError('PPT has no slides or exceeds the slide-count limit.')
    return {'kind': 'ppt', 'format': suffix[1:].upper(), 'pages': len(deck.slides), 'width': round(deck.slide_width.pt), 'height': round(deck.slide_height.pt), 'unit': 'pt', 'render_path': str(pdf), 'pptx_path': str(pptx_path), 'warnings': ['Slides render as static images. Animation, audio and video are not exported.']}


def assemble_pptx(items, options, output, quality, scale, pixel_limit, progress, output_limit):
    from .engine import direct_image, encode, fit_output, load_image, rgb_image
    deck = Presentation()
    margin = int(Mm(options.margin_mm))
    first = items[0]
    with load_image(first, options, pixel_limit) as first_image:
        if first['kind'] == 'ppt':
            original = Presentation(first['pptx_path'])
            width, height = int(original.slide_width), int(original.slide_height)
            if first.get('rotation') in (90, 270):
                width, height = height, width
        else:
            width, height = int(Inches(first_image.width / options.dpi)), int(Inches(first_image.height / options.dpi))
        if options.paper == 'wide':
            width, height = int(Inches(13.333333)), int(Inches(7.5))
        elif options.paper == 'standard':
            width, height = int(Inches(10)), int(Inches(7.5))
        elif options.paper in ('a4', 'letter'):
            width, height = (int(Mm(210)), int(Mm(297))) if options.paper == 'a4' else (int(Inches(8.5)), int(Inches(11)))
            if first_image.width > first_image.height:
                width, height = height, width
        else:
            width, height = width + 2 * margin, height + 2 * margin
    # PowerPoint supports a single slide size per presentation, between 1 and 56 inches.
    if not all(int(Inches(1)) <= length <= int(Inches(56)) for length in (width, height)):
        raise ValueError('PPT slide dimensions must be between 1 and 56 inches. Choose 16:9 or 4:3 slide size.')
    if min(width, height) <= 2 * margin:
        raise ValueError('Margins leave no space for the slide image.')
    deck.slide_width, deck.slide_height = width, height
    page_target_met = True
    originals = 0
    for index, item in enumerate(items):
        with load_image(item, options, pixel_limit, scale) as image:
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string(options.background[1:])
            encoded = io.BytesIO()
            if item['kind'] == 'image' and not options.lossless:
                with tempfile.TemporaryDirectory(dir=Path(output).parent) as work:
                    original = direct_image(item, options, Path(work) / 'image.jpg')
                    if original:
                        with Image.open(original) as raw:
                            # PNG metadata must be removed by encoding when requested.
                            if raw.format == 'JPEG' or not options.strip_metadata:
                                encoded.write(Path(original).read_bytes())
                                originals += 1
            if not encoded.getbuffer().nbytes:
                if options.lossless or options.color == 'bw':
                    encode(image, encoded, 'png', quality, options)
                else:
                    with rgb_image(image, options.background) as rgb:
                        encode(rgb, encoded, 'jpeg', quality, options)
            if options.page_image_kb:
                with tempfile.TemporaryDirectory(dir=Path(output).parent) as work:
                    candidate = Path(work) / 'image.bin'
                    page_options = options.model_copy(update={'mode': 'target', 'target_kb': options.page_image_kb, 'quality': quality, 'min_quality': min(options.min_quality, quality), 'allow_downscale': False})
                    def writer(path, candidate_quality, candidate_scale):
                        encode(image, path, 'png' if options.lossless or options.color == 'bw' else 'jpeg', candidate_quality, options)
                    result = fit_output(writer, page_options, candidate, lossy=not options.lossless and options.color != 'bw')
                    page_target_met = page_target_met and result['target_met']
                    encoded = io.BytesIO(candidate.read_bytes())
            ratio = min((width - 2 * margin) / image.width, (height - 2 * margin) / image.height)
            picture_width, picture_height = max(1, round(image.width * ratio)), max(1, round(image.height * ratio))
            encoded.seek(0)
            slide.shapes.add_picture(encoded, (width - picture_width) // 2, (height - picture_height) // 2, width=picture_width, height=picture_height)
        progress({'detail': f'Assembling slide {index + 1} of {len(items)}'})
    if options.strip_metadata:
        for attribute in ('author', 'last_modified_by', 'title', 'subject', 'keywords', 'comments', 'category', 'identifier'):
            setattr(deck.core_properties, attribute, '')
    deck.save(str(output))
    if Path(output).stat().st_size > output_limit:
        raise ValueError('Results exceed the output safety limit. Use fewer slides or smaller dimensions.')
    result = {'page_image_target_met': page_target_met, 'original_pages': originals}
    if originals == len(items):
        result['quality'] = None
    return result


def preserve_pptx(items, options, output):
    if any(item.get('rotation') for item in items):
        raise ValueError('Content-preserving compression cannot rotate slides. Reset rotation or choose rasterized compression.')
    deck = Presentation(items[0]['pptx_path'])
    selected = [item['page'] for item in items]
    if len(selected) != len(set(selected)):
        raise ValueError('Content-preserving compression cannot duplicate slides.')
    # Reorder the existing slide IDs so references and editable shapes stay attached.
    ids = deck.slides._sldIdLst
    existing = list(ids)
    for index, slide_id in enumerate(existing):
        ids.remove(slide_id)
        if index not in selected:
            deck.part.drop_rel(slide_id.rId)
    for index in selected:
        ids.append(existing[index])
    if options.strip_metadata:
        for attribute in ('author', 'last_modified_by', 'title', 'subject', 'keywords', 'comments', 'category', 'identifier'):
            setattr(deck.core_properties, attribute, '')
    content = io.BytesIO()
    deck.save(content)
    content.seek(0)
    with zipfile.ZipFile(content) as original, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as optimized:
        for item in original.infolist():
            optimized.writestr(item.filename, original.read(item.filename))


def pptx_preview(path, pixel_limit):
    from .engine import check_pixels
    deck = Presentation(str(path))
    shape = next(shape for shape in deck.slides[0].shapes if shape.shape_type == 13)
    with Image.open(io.BytesIO(shape.image.blob)) as embedded:
        check_pixels(*embedded.size, pixel_limit)
        image = embedded.convert('RGBA')
    width, height = int(deck.slide_width), int(deck.slide_height)
    ratio = min(2400 / width, 2400 / height)
    canvas = Image.new('RGB', (max(1, round(width * ratio)), max(1, round(height * ratio))), '#' + str(deck.slides[0].background.fill.fore_color.rgb))
    image = image.resize((max(1, round(shape.width * ratio)), max(1, round(shape.height * ratio))), Image.Resampling.LANCZOS)
    canvas.paste(image, (round(shape.left * ratio), round(shape.top * ratio)), image)
    image.close()
    return canvas
