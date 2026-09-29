"""Image classification: brightness-heuristic stub until the CNN is trained, then MobileNetV3."""

import io
import json

from PIL import Image, ImageStat

from .config import settings

WEIGHTS = {"clean": 0, "dusty": 50, "bird_dropping": 60, "mixed": 80}


class StubClassifier:
    """Lets the rest of the system be built and tested before the CNN exists."""

    def predict(self, data: bytes):
        gray = Image.open(io.BytesIO(data)).convert("L").resize((32, 32))
        avg = ImageStat.Stat(gray).mean[0]
        sev = max(0.0, min(100.0, (avg - 90) * 1.2))
        return ("dusty" if sev > 20 else "clean"), 0.5, sev


class CnnClassifier:
    def __init__(self):
        import torch  # imported lazily so the API runs without torch installed
        from torchvision import models, transforms

        self.torch = torch
        with open(settings.classes_path) as f:
            self.classes = json.load(f)
        m = models.mobilenet_v3_small()
        m.classifier[3] = torch.nn.Linear(m.classifier[3].in_features, len(self.classes))
        m.load_state_dict(torch.load(settings.model_path, map_location="cpu"))
        self.model = m.eval()
        self.tf = transforms.Compose(
            [
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )

    def predict(self, data: bytes):
        x = self.tf(Image.open(io.BytesIO(data)).convert("RGB")).unsqueeze(0)
        with self.torch.no_grad():
            probs = self.torch.softmax(self.model(x), dim=1)[0]
        sev = sum(float(p) * WEIGHTS[c] for p, c in zip(probs, self.classes))
        i = int(probs.argmax())
        return self.classes[i], float(probs[i]), sev


def load_classifier():
    return CnnClassifier() if settings.model_path.exists() else StubClassifier()
