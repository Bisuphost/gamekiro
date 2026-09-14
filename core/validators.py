from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible


@deconstructible
class MaxFileSizeValidator:
    def __init__(self, max_mb):
        self.max_mb = max_mb
        self.max_bytes = max_mb * 1024 * 1024

    def __call__(self, file):
        if file.size > self.max_bytes:
            raise ValidationError(f"File must be {self.max_mb}MB or smaller.")

    def __eq__(self, other):
        return isinstance(other, MaxFileSizeValidator) and self.max_mb == other.max_mb


validate_image_file_size = MaxFileSizeValidator(5)
