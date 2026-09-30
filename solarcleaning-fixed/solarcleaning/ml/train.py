"""Train the soiling classifier (MobileNetV3-small, transfer learning).

Example:
  python ml/train.py --data ml/data --test-sessions own_panel_01 --epochs 15 \
      --out ml/models/soiling_mnv3.pt

Writes the checkpoint (best epoch by validation macro-F1) and <out-dir>/report.txt.
Requires: pip install -r ml/requirements-ml.txt
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, ImageOps
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splits import class_weights, scan_dataset, split_by_session  # noqa: E402

CLASSES = ["clean", "dusty", "bird_drop", "mixed"]  # keep in sync with backend/app/vision.py
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class PanelDataset(Dataset):
    def __init__(self, items, transform):
        self.items, self.transform = items, transform

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, label, _ = self.items[i]
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        return self.transform(img), label


def make_transforms(size: int):
    train_tf = transforms.Compose(
        [
            transforms.RandomResizedCrop(size, scale=(0.7, 1.0)),
            transforms.RandomHorizontalFlip(),
            transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2),
            transforms.ToTensor(),
            transforms.Normalize(MEAN, STD),
        ]
    )
    eval_tf = transforms.Compose(
        [transforms.Resize((size, size)), transforms.ToTensor(), transforms.Normalize(MEAN, STD)]
    )
    return train_tf, eval_tf


def build_net(num_classes: int, pretrained: bool) -> nn.Module:
    weights = models.MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
    net = models.mobilenet_v3_small(weights=weights)
    net.classifier[3] = nn.Linear(net.classifier[3].in_features, num_classes)
    return net


@torch.no_grad()
def predict(net, loader, device):
    net.eval()
    ys, ps = [], []
    for x, y in loader:
        ps.extend(net(x.to(device)).argmax(1).cpu().tolist())
        ys.extend(y.tolist())
    return ys, ps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--test-sessions", required=True, help="comma list, e.g. own_panel_01")
    ap.add_argument("--out", type=Path, default=Path("ml/models/soiling_mnv3.pt"))
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--img-size", type=int, default=224)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-pretrained", action="store_true", help="skip ImageNet weights")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    seed_everything(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    items = scan_dataset(args.data, CLASSES)
    if not items:
        sys.exit(f"No images found under {args.data}/<session>/<class>/")
    tests = [s.strip() for s in args.test_sessions.split(",") if s.strip()]
    train, val, test, warnings = split_by_session(items, tests, seed=args.seed)
    print(f"train={len(train)} val={len(val)} test={len(test)} device={device}")

    train_tf, eval_tf = make_transforms(args.img_size)
    gen = torch.Generator().manual_seed(args.seed)
    dl = lambda ds, shuffle: DataLoader(  # noqa: E731
        ds,
        batch_size=args.batch_size,
        shuffle=shuffle,
        num_workers=args.workers,
        generator=gen if shuffle else None,
    )
    train_dl = dl(PanelDataset(train, train_tf), True)
    val_dl = dl(PanelDataset(val, eval_tf), False)
    test_dl = dl(PanelDataset(test, eval_tf), False)

    weights = torch.tensor(class_weights([y for _, y, _ in train], len(CLASSES)), dtype=torch.float)
    net = build_net(len(CLASSES), pretrained=not args.no_pretrained).to(device)
    loss_fn = nn.CrossEntropyLoss(weight=weights.to(device))
    opt = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)

    best_f1, best_state, best_epoch = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        net.train()
        running = 0.0
        for x, y in train_dl:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = loss_fn(net(x), y)
            loss.backward()
            opt.step()
            running += loss.item() * len(y)
        sched.step()
        ys, ps = predict(net, val_dl, device)
        f1 = f1_score(ys, ps, average="macro", labels=list(range(len(CLASSES))), zero_division=0)
        print(f"epoch {epoch:02d} loss={running / len(train):.4f} val_macro_f1={f1:.4f}")
        if f1 > best_f1:
            best_f1, best_epoch = f1, epoch
            best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}

    net.load_state_dict(best_state)
    ys, ps = predict(net, test_dl, device)
    labels = list(range(len(CLASSES)))
    test_f1 = f1_score(ys, ps, average="macro", labels=labels, zero_division=0)
    version = f"mnv3s-{time.strftime('%Y%m%d-%H%M')}-f1_{test_f1:.2f}"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": best_state,
            "classes": CLASSES,
            "model_version": version,
            "img_size": args.img_size,
            "mean": list(MEAN),
            "std": list(STD),
        },
        args.out,
    )
    report = [
        f"model_version: {version}",
        f"best_epoch: {best_epoch} (val macro-F1 {best_f1:.4f})",
        f"seed: {args.seed}",
        f"images: train={len(train)} val={len(val)} test={len(test)}",
        f"test sessions: {', '.join(tests)}",
        f"class weights: {[round(float(w), 3) for w in weights]}",
        *[f"WARNING: {w}" for w in warnings],
        "",
        f"HELD-OUT TEST macro-F1: {test_f1:.4f}",
        classification_report(ys, ps, labels=labels, target_names=CLASSES, zero_division=0),
        "confusion matrix (rows = true, cols = predicted):",
        str(confusion_matrix(ys, ps, labels=labels)),
    ]
    text = "\n".join(report)
    (args.out.parent / "report.txt").write_text(text)
    print(text)
    print(f"\nSaved {args.out}")


if __name__ == "__main__":
    main()
