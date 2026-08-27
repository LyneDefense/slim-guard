class ImageAssetError(RuntimeError):
    """Base class for authoritative image asset failures."""


class ImageAssetCollision(ImageAssetError):
    pass
