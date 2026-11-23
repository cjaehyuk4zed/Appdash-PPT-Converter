"""Strict API validation without a native validation runtime."""
import math
import re
from dataclasses import asdict, dataclass, fields, replace


def validate_object(value, allowed):
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    if unknown := set(value) - set(allowed):
        raise ValueError("Unknown setting: " + ", ".join(sorted(unknown)))


@dataclass
class Selection:
    file_id: str
    page: int = 0
    rotation: int = 0

    @classmethod
    def model_validate(cls, value):
        validate_object(value, ("file_id", "page", "rotation"))
        if not isinstance(value.get("file_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["file_id"]):
            raise ValueError("Invalid file identifier.")
        if type(value.get("page", 0)) is not int or not 0 <= value.get("page", 0) <= 9999:
            raise ValueError("Invalid page number.")
        if type(value.get("rotation", 0)) is not int or value.get("rotation", 0) not in (0, 90, 180, 270):
            raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.")
        return cls(**value)


@dataclass
class Options:
    operation: str
    format: str = "jpeg"
    mode: str = "quality"
    quality: int = 85
    min_quality: int = 60
    lossless: bool = False
    dpi: int = 200
    min_dpi: int = 100
    max_width: int | None = None
    max_height: int | None = None
    target_kb: float | None = None
    page_image_kb: float | None = None
    allow_downscale: bool = True
    min_long_edge: int = 1024
    color: str = "original"
    threshold: int = 180
    background: str = "#ffffff"
    strip_metadata: bool = True
    paper: str = "original"
    margin_mm: float = 0
    split: bool = False
    preserve_original: bool = True
    compression: str = "preserve"
    pages: str = ""

    @classmethod
    def model_validate(cls, value):
        validate_object(value, (field.name for field in fields(cls)))
        if "operation" not in value:
            raise ValueError("Choose a conversion operation.")
        options = cls(**value)
        choices = {"operation": ("ppt_to_images", "images_to_ppt", "image_convert", "ppt_compress"), "format": ("jpeg", "png", "webp", "tiff", "bmp", "avif"), "mode": ("quality", "target"), "color": ("original", "grayscale", "bw"), "paper": ("original", "wide", "standard", "a4", "letter"), "compression": ("preserve", "raster")}
        for name, allowed in choices.items():
            if getattr(options, name) not in allowed:
                raise ValueError(f"Invalid {name} setting.")
        integers = {"quality": (1, 95), "min_quality": (1, 95), "dpi": (36, 600), "min_dpi": (36, 600), "min_long_edge": (64, 20000), "threshold": (0, 255), "max_width": (1, 20000), "max_height": (1, 20000)}
        for name, (minimum, maximum) in integers.items():
            setting = getattr(options, name)
            if setting is None and name in ("max_width", "max_height"):
                continue
            if type(setting) is not int or not minimum <= setting <= maximum:
                raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
        for name in ("lossless", "allow_downscale", "strip_metadata", "split", "preserve_original"):
            if type(getattr(options, name)) is not bool:
                raise ValueError(f"{name} must be true or false.")
        for name in ("target_kb", "page_image_kb", "margin_mm"):
            setting = getattr(options, name)
            if setting is None and name != "margin_mm":
                continue
            minimum, maximum = (0, 50) if name == "margin_mm" else (0.001, 1024000)
            if type(setting) not in (int, float) or not math.isfinite(setting) or not minimum <= setting <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}.")
        if not isinstance(options.background, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", options.background):
            raise ValueError("Choose a valid background color.")
        if not isinstance(options.pages, str) or len(options.pages) > 2000:
            raise ValueError("Page range is too long.")
        if options.mode == "target" and options.target_kb is None:
            raise ValueError("Enter a maximum output file size for target-size mode.")
        raster = options.operation != "ppt_compress" or options.compression == "raster"
        lossy = options.format in ("jpeg", "webp", "avif") if options.operation in ("image_convert", "ppt_to_images") else options.color != "bw"
        if raster and lossy and not options.lossless and (options.mode == "target" or options.page_image_kb) and options.min_quality > options.quality:
            raise ValueError("Minimum quality must not exceed the selected quality.")
        if raster and options.operation in ("ppt_to_images", "ppt_compress") and (options.mode == "target" or options.page_image_kb) and options.allow_downscale and options.min_dpi > options.dpi:
            raise ValueError("Minimum DPI must not exceed the rendering DPI.")
        return options

    def model_dump(self):
        return asdict(self)

    def model_copy(self, *, update):
        return replace(self, **update)


@dataclass
class JobRequest:
    selections: list[Selection]
    options: Options
    preview: bool = False

    @classmethod
    def model_validate(cls, value):
        validate_object(value, ("selections", "options", "preview"))
        selections = value.get("selections")
        if not isinstance(selections, list) or not 1 <= len(selections) <= 500:
            raise ValueError("Select between 1 and 500 pages.")
        if type(value.get("preview", False)) is not bool:
            raise ValueError("Preview must be true or false.")
        return cls([Selection.model_validate(selection) for selection in selections], Options.model_validate(value.get("options")), value.get("preview", False))
