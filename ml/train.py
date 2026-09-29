"""Train the soiling classifier (MobileNetV3-Small, transfer learning). Run from inside ml/.

Expects ml/data/{train,val,test}/{clean,dusty,bird_dropping,mixed}/*.jpg
Split by photo SESSION, not randomly, or near-duplicate frames leak into the test set.
Needs: pip install -r backend/requirements-ml.txt
"""

import json
import os
import sys

import torch
import torch.nn as nn
from sklearn.metrics import classification_report, confusion_matrix
from torch.utils.data import DataLoader
from torchvision import datasets, models, transforms

if not os.path.isdir("data/train"):
    sys.exit("data/train not found - run this from inside ml/ after adding your dataset.")

norm = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
train_tf = transforms.Compose(
    [
        transforms.RandomResizedCrop(224, scale=(0.7, 1)),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.3, 0.3, 0.2),
        transforms.ToTensor(),
        norm,
    ]
)
eval_tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(), norm])

train = datasets.ImageFolder("data/train", train_tf)
val = datasets.ImageFolder("data/val", eval_tf)
test = datasets.ImageFolder("data/test", eval_tf)


def dl(d, shuffle=False):
    return DataLoader(d, batch_size=32, shuffle=shuffle)


model = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.DEFAULT)
model.classifier[3] = nn.Linear(model.classifier[3].in_features, len(train.classes))
for p in model.features.parameters():
    p.requires_grad = False  # stage 1: head only


def run(epochs, lr):
    opt = torch.optim.Adam((p for p in model.parameters() if p.requires_grad), lr=lr)
    for ep in range(epochs):
        model.train()
        for x, y in dl(train, True):
            opt.zero_grad()
            nn.functional.cross_entropy(model(x), y).backward()
            opt.step()
        model.eval()
        correct = 0
        with torch.no_grad():
            for x, y in dl(val):
                correct += (model(x).argmax(1) == y).sum().item()
        print(f"epoch {ep}: val acc {correct / len(val):.3f}")


run(5, 1e-3)
for p in model.features[-4:].parameters():
    p.requires_grad = True  # stage 2: fine-tune last blocks
run(5, 1e-4)

model.eval()
ys, ps = [], []
with torch.no_grad():
    for x, y in dl(test):
        ys += y.tolist()
        ps += model(x).argmax(1).tolist()
report = classification_report(ys, ps, target_names=test.classes)
print(report)
print(confusion_matrix(ys, ps))

os.makedirs("artifacts", exist_ok=True)
torch.save(model.state_dict(), "artifacts/soiling_mnv3.pt")
json.dump(train.classes, open("artifacts/classes.json", "w"))  # alphabetical, matches inference
with open("artifacts/report.txt", "w") as f:
    f.write(report + "\n" + str(confusion_matrix(ys, ps)) + "\n")
