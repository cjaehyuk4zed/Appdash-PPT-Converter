"""Conversion code. Called only inside disposable worker processes."""
import io
import math
import os
import re
import shutil
import warnings
import zipfile
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageCms, ImageFile, ImageOps, features
from pillow_heif import register_heif_opener

from .models import Options
from .presentation import assemble_pptx, inspect_presentation, preserve_pptx

register_heif_opener()
warnings.simplefilter("error", Image.DecompressionBombWarning)
EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp", "tiff": "tif", "bmp": "bmp", "avif": "avif"}
INPUT_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF", "AVIF", "HEIF"}


def capabilities():
    Image.init()
    formats = [f for f in EXTENSIONS if f.upper() in Image.SAVE]
    if not features.check("webp"):
        formats = [f for f in formats if f != "webp"]
    if not features.check("avif"):
        formats = [f for f in formats if f != "avif"]
    return {"input": sorted(INPUT_FORMATS & set(Image.OPEN)), "output": formats}


def safe_stem(name):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).stem).strip(". ")[:100]
    return value or "converted"


def page_indices(expression, count):
    if not expression.strip():
        return list(range(count))
    result = []
    for segment in expression.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", segment)
        if not match:
            raise ValueError("Use page ranges such as 1-3, 7, 10-12.")
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= count:
            raise ValueError(f"Page range must be between 1 and {count}.")
        for number in range(first - 1, last):
            if number not in result:
                result.append(number)
    return result


def check_pixels(width, height, limit):
    if width <= 0 or height <= 0 or width * height > limit:
        raise ValueError(f"Image/render exceeds the {limit:,}-pixel safety limit. Lower the DPI or dimensions.")


def render_pdf(item, dpi, pixel_limit, max_width=None, max_height=None):
    with pdfium.PdfDocument(item.get("render_path", item["path"]), password=item.get("password") or None) as pdf:
        pdf.init_forms()
        with closing(pdf.get_page(item.get("page", 0))) as page:
            width, height = page.get_size()
            scale = dpi / 72
            if max_width:
                scale = min(scale, max_width / width)
            if max_height:
                scale = min(scale, max_height / height)
            check_pixels(math.ceil(width * scale), math.ceil(height * scale), pixel_limit)
            bitmap = page.render(scale=scale, draw_annots=True)
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()


def load_image(item, options, pixel_limit, scale=1):
    if item["kind"] == "ppt":
        bounds = (options.max_width, options.max_height)
        if item.get("rotation") in (90, 270):
            bounds = bounds[::-1]
        image = render_pdf(item, options.dpi * scale, pixel_limit, *bounds)
    else:
        with Image.open(item["path"]) as source:
            source.seek(item.get("page", 0))
            check_pixels(*source.size, pixel_limit)
            image = ImageOps.exif_transpose(source).copy()
        if item.get("rotation"):
            image = image.rotate(-item["rotation"], expand=True)
        if options.max_width or options.max_height:
            image.thumbnail((options.max_width or image.width, options.max_height or image.height), Image.Resampling.LANCZOS)
        floor = min(1, options.min_long_edge / max(image.size))
        actual_scale = max(scale, floor)
        if actual_scale < 1:
            image = image.resize(tuple(max(1, round(v * actual_scale)) for v in image.size), Image.Resampling.LANCZOS)
    if item["kind"] == "ppt" and item.get("rotation"):
        image = image.rotate(-item["rotation"], expand=True)
    if options.color in ("grayscale", "bw"):
        alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
        image = image.convert("L")
        if alpha is not None:
            background = Image.new("L", image.size, 255)
            background.paste(image, mask=alpha)
            image = background
        if options.color == "bw":
            image = image.point(lambda value: 255 if value >= options.threshold else 0, mode="1")
    return image


def rgb_image(image, background):
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        out = Image.new("RGB", image.size, background)
        out.paste(rgba, mask=rgba.getchannel("A"))
        return out
    profile = image.info.get("icc_profile")
    if profile and image.mode in ("RGB", "CMYK"):
        try:
            return ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ValueError, OSError, ImageCms.PyCMSError):
            pass
    return image.convert("RGB")


def encode(image, path, fmt, quality, options):
    args = {}
    if fmt == "jpeg":
        image = rgb_image(image, options.background)
        args.update(quality=quality, optimize=True, progressive=True, subsampling=0 if quality >= 90 else 2)
    elif fmt in ("webp", "avif"):
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        args.update(quality=quality)
        if fmt == "webp":
            args.update(lossless=options.lossless, method=4)
        elif options.lossless:
            raise ValueError("Use PNG, TIFF or lossless WebP for lossless output.")
    elif fmt == "png":
        args.update(optimize=True, compress_level=9)
        if image.mode == "CMYK":
            image = rgb_image(image, options.background)
    elif fmt == "tiff":
        args.update(compression="group4" if image.mode == "1" else "tiff_deflate")
    elif fmt == "bmp":
        image = rgb_image(image, options.background)
    if not options.strip_metadata:
        # Orientation is already applied to pixels; never write the old orientation again.
        exif = image.getexif()
        exif.pop(274, None)
        if exif and fmt in ("jpeg", "png", "webp", "tiff", "avif"):
            args["exif"] = exif.tobytes()
    old_block = ImageFile.MAXBLOCK
    try:
        # Pillow's optimized JPEG estimate is too small for high-entropy 4:4:4 images.
        if fmt == "jpeg":
            ImageFile.MAXBLOCK = max(old_block, image.width * image.height * 4 + len(args.get("exif", b"")) + 65536)
        image.save(path, format=fmt.upper(), **args)
    finally:
        ImageFile.MAXBLOCK = old_block


def fit_output(writer, options, destination, floor_scale=1, lossy=True, progress=None):
    """Bounded search; retain the best candidate and verify bytes on the finished file."""
    destination = Path(destination)
    trial = destination.with_name("_trial" + destination.suffix)
    best = destination.with_name("_best" + destination.suffix)
    target = math.floor(options.target_kb * 1000) if options.mode == "target" else None
    best_size = math.inf
    best_settings = None
    attempts = 0
    if target and options.allow_downscale and floor_scale < 1:
        scales = [floor_scale ** (i / 5) for i in range(6)]
    else:
        scales = [1.0]

    def attempt(quality, scale):
        nonlocal attempts, best_size, best_settings
        info = writer(trial, quality, scale) or {}
        size = trial.stat().st_size
        attempts += 1
        if size < best_size:
            shutil.copyfile(trial, best)
            best_size = size
            best_settings = {"quality": quality if lossy else None, "scale": round(scale, 4), **info}
        if progress:
            progress({"detail": f"Testing output size · attempt {attempts}"})
        return size, info

    try:
        for scale in scales:
            size, info = attempt(options.quality, scale)
            if target is None or size <= target:
                shutil.copyfile(trial, destination)
                return {"target_met": None if target is None else True, "quality": options.quality if lossy else None, "scale": round(scale, 4), "attempts": attempts, **info}
            if not lossy or options.min_quality == options.quality:
                continue
            size, info = attempt(options.min_quality, scale)
            if size > target:
                continue
            shutil.copyfile(trial, destination)
            winner = {"quality": options.min_quality, "scale": round(scale, 4), **info}
            low, high = options.min_quality, options.quality
            while high - low > 1:
                quality = (low + high) // 2
                size, info = attempt(quality, scale)
                if size <= target:
                    shutil.copyfile(trial, destination)
                    winner = {"quality": quality, "scale": round(scale, 4), **info}
                    low = quality
                else:
                    high = quality
            return {"target_met": True, "attempts": attempts, **winner}
        shutil.copyfile(best, destination)
        return {"target_met": False, "attempts": attempts, **best_settings}
    finally:
        trial.unlink(missing_ok=True)
        best.unlink(missing_ok=True)


def inspect_file(payload):
    path = Path(payload["path"])
    Image.MAX_IMAGE_PIXELS = payload["pixel_limit"]
    notes = []
    if Path(payload.get("name", path.name)).suffix.lower() in (".ppt", ".pptx"):
        info = inspect_presentation(payload)
        notes.extend(info.pop("warnings"))
        image = render_pdf({"path": info["render_path"]}, 72, payload["pixel_limit"], 360, 440)
    else:
        try:
            with Image.open(path) as source:
                if source.format not in INPUT_FORMATS:
                    raise ValueError("Unsupported image format.")
                check_pixels(*source.size, payload["pixel_limit"])
                fmt = source.format
                count = getattr(source, "n_frames", 1) if fmt == "TIFF" else 1
                if count > payload["page_limit"]:
                    raise ValueError("Image exceeds the page-count limit.")
                if fmt != "TIFF" and getattr(source, "n_frames", 1) > 1:
                    notes.append("Only the first frame/primary image will be imported.")
                page_sizes = []
                for index in range(count):
                    source.seek(index)
                    check_pixels(*source.size, payload["pixel_limit"])
                    size = source.size
                    if source.getexif().get(274) in (5, 6, 7, 8):
                        size = size[::-1]
                    page_sizes.append(list(size))
                source.seek(0)
                image = ImageOps.exif_transpose(source).copy()
                info = {"kind": "image", "format": fmt, "pages": count, "width": image.width, "height": image.height, "unit": "px", "page_sizes": page_sizes}
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("Cannot open image, or it exceeds the pixel safety limit.") from error
    image.thumbnail((360, 440), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        image = image.convert("RGB")
    image.save(payload["thumbnail"], "PNG")
    image.close()
    return {**info, "warnings": notes}


def page_thumbnail(payload):
    Image.MAX_IMAGE_PIXELS = payload["pixel_limit"]
    image = load_image(payload["item"], Options(operation="image_convert", dpi=72, max_width=360, max_height=440), payload["pixel_limit"])
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        image = image.convert("RGB")
    path = Path(payload["thumbnail"])
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        image.save(temporary, "PNG")
        temporary.replace(path)
    finally:
        image.close()
        temporary.unlink(missing_ok=True)
    return {}


def strip_jpeg_metadata(source, destination):
    """Remove EXIF/XMP/comments without recompressing JPEG scan data."""
    data = Path(source).read_bytes()
    output = bytearray(data[:2])
    position = 2
    while position < len(data):
        if data[position] != 0xFF:
            raise ValueError("Invalid JPEG marker.")
        start = position
        while position < len(data) and data[position] == 0xFF:
            position += 1
        marker = data[position]
        position += 1
        if marker in (0xDA, 0xD9):
            output.extend(data[start:])
            break
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("Invalid JPEG metadata segment.")
        if marker not in (0xE1, 0xED, 0xFE):
            output.extend(data[start:position + length])
        position += length
    Path(destination).write_bytes(output)


def direct_image(item, options, destination):
    if not options.preserve_original or options.mode == "target" or options.page_image_kb or options.max_width or options.max_height or options.color != "original" or item.get("rotation"):
        return None
    with Image.open(item["path"]) as source:
        if source.getexif().get(274, 1) != 1 or item.get("page", 0):
            return None
        if source.format not in ("JPEG", "PNG"):
            return None
        if source.format == "JPEG" and options.strip_metadata:
            strip_jpeg_metadata(item["path"], destination)
            return str(destination)
        return item["path"]


def conversion(payload, progress):
    options = Options.model_validate(payload["options"])
    root = Path(payload["directory"])
    pixel_limit = payload["pixel_limit"]
    output_limit = payload.get("output_limit", 500000000)
    Image.MAX_IMAGE_PIXELS = pixel_limit
    items = payload["items"]
    if options.pages and options.operation in ("ppt_to_images", "ppt_compress"):
        filtered = []
        for file_id in dict.fromkeys(item["file_id"] for item in items):
            group = [item for item in items if item["file_id"] == file_id]
            indices = page_indices(options.pages, group[0]["pages"])
            by_page = {item["page"]: item for item in group}
            filtered.extend(by_page[index] for index in indices if index in by_page)
        items = filtered
    if not items:
        raise ValueError("No pages selected after applying the page range.")
    if payload.get("preview"):
        items = items[:1]
    notes = []
    if options.operation == "images_to_ppt":
        notes.append("Each slide contains one image. Text inside the image is not editable.")
    outputs = []
    image_output = options.operation in ("ppt_to_images", "image_convert")
    if image_output and options.format not in capabilities()["output"]:
        raise ValueError("This output codec is unavailable on this server.")
    groups = [[item] for item in items] if image_output or options.split else [items]
    if options.operation == "ppt_compress" and not options.split:
        groups = [[item for item in items if item["file_id"] == file_id] for file_id in dict.fromkeys(item["file_id"] for item in items)]
    for index, group in enumerate(groups):
        item = group[0]
        extension = EXTENSIONS[options.format] if image_output else "pptx"
        name = f"{safe_stem(item['name'])}_{index + 1:03d}"
        destination = root / f"{name}.{extension}"
        progress({"progress": round(index / len(groups) * 90), "detail": f"Converting output {index + 1} of {len(groups)}"})
        if image_output:
            base = load_image(item, options, pixel_limit)
            try:
                floor = min(1, options.min_dpi / options.dpi) if item["kind"] == "ppt" else min(1, options.min_long_edge / max(base.size))
                def writer(path, quality, scale, source_image=base):
                    # Every attempt starts from the original decoded pixels, never the last JPEG.
                    image = source_image if scale == 1 else source_image.resize(tuple(max(1, round(v * scale)) for v in source_image.size), Image.Resampling.LANCZOS)
                    try:
                        encode(image, path, options.format, quality, options)
                        return {"width": image.width, "height": image.height}
                    finally:
                        if image is not source_image:
                            image.close()
                lossy = options.format in ("jpeg", "webp", "avif") and not options.lossless
                result = fit_output(writer, options, destination, floor, lossy, progress)
            finally:
                base.close()
        elif options.operation == "ppt_compress" and options.compression == "preserve":
            preserve_pptx(group, options, destination)
            target = math.floor(options.target_kb * 1000) if options.mode == "target" else None
            result = {"target_met": destination.stat().st_size <= target if target else None, "quality": None, "scale": 1, "attempts": 1}
            notes.append("Structure optimization keeps slide content without reducing embedded image resolution. Size savings vary. Editing invalidates digital signatures.")
        else:
            if options.operation == "ppt_compress":
                notes.append("Rasterized slides are flat images. Editable text, shapes, links, animation, audio and video are removed.")
            floor = min(1, options.min_dpi / options.dpi) if item["kind"] == "ppt" else 0.08
            def writer(path, quality, scale, output_group=group):
                return assemble_pptx(output_group, options, path, quality, scale, pixel_limit, progress, output_limit)
            result = fit_output(writer, options, destination, floor, not options.lossless and options.color != "bw", progress)
        if result.get("target_met") is False:
            notes.append("A size target could not be met within the selected quality/resolution limits. The smallest attempted result is available.")
        if result.get("page_image_target_met") is False:
            notes.append("At least one embedded page image exceeds the page-image budget.")
        outputs.append({"name": destination.name, "size": destination.stat().st_size, "pages": len(group), **result})
        if sum(output["size"] for output in outputs) > output_limit:
            raise ValueError("Results exceed the output safety limit. Use fewer pages or smaller dimensions.")
    preview_path = root / "preview.png"
    first = root / outputs[0]["name"]
    if first.suffix == ".pptx":
        if options.operation == "ppt_compress" and options.compression == "preserve":
            preview_image = render_pdf(items[0], options.dpi, pixel_limit, 2400, 2400)
        else:
            from .presentation import pptx_preview
            preview_image = pptx_preview(first, pixel_limit)
    else:
        with Image.open(first) as decoded:
            preview_image = decoded.copy()
        preview_image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    if preview_image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        preview_image = preview_image.convert("RGB")
    preview_image.save(preview_path, "PNG")
    preview_image.close()
    with zipfile.ZipFile(root / "results.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for output in outputs:
            archive.write(root / output["name"], output["name"])
    return {"outputs": outputs, "warnings": list(dict.fromkeys(notes)), "preview": bool(payload.get("preview"))}
