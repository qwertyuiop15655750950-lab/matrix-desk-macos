"""UI image helper shared by platform handlers."""
import base64
import io

def _thumb_data_uri(path, width=360):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    img = img.resize((width, int(h * width / w)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

