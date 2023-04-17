from typing import List
import inspect
import sys

import torch
import torch.nn as nn

from torchvision.models import AlexNet_Weights
from torchvision.transforms import Compose, Resize, CenterCrop, ToTensor, Normalize

import resnet as rn


def get_image_net_preprocessor():
    return Compose(
        [
            Resize(256),
            CenterCrop(224),
            ToTensor(),
            Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


def load_alexnet() -> nn.Module:
    model = torch.hub.load(
        "pytorch/vision:v0.10.0", "alexnet", weights=AlexNet_Weights.DEFAULT
    )
    model.eval()
    preprocess = get_image_net_preprocessor()
    return model, preprocess


def load_alexnet_softmax() -> nn.Module:
    model, preprocess = load_alexnet()
    model = nn.Sequential(model, nn.Softmax(dim=1))
    return model, preprocess


def load_alexnet_fc6() -> nn.Module:
    model, preprocess = load_alexnet()
    model.classifier = model.classifier[:2]
    return model, preprocess


def load_resnet50() -> nn.Module:
    model = torch.hub.load("pytorch/vision:v0.10.0", "resnet18", pretrained=True)
    model.eval()
    preprocess = get_image_net_preprocessor()
    return model, preprocess


def load_resnet50_softmax() -> nn.Module:
    model, preprocess = load_resnet50()
    model = nn.Sequential(model, nn.Softmax(dim=1))
    return model, preprocess


def load_simclr2_r50_2x_sk1() -> nn.Module:
    # load in the model
    preprocess = get_image_net_preprocessor()
    pth_path = "/models/r50_2x_sk1.pth"
    model, _ = rn.get_resnet(*rn.name_to_params(pth_path))
    model.load_state_dict(torch.load(pth_path)["resnet"])
    model.eval()

    # remove the projection head
    model.fc = nn.Identity()

    return model, preprocess


def get_loader_names() -> List[str]:
    def is_loader(name, obj):
        return (
            inspect.isfunction(obj)
            and name.startswith("load")
            and obj.__module__ == __name__
        )

    return [
        name
        for name, obj in inspect.getmembers(sys.modules[__name__])
        if is_loader(name, obj)
    ]


def get_model(model_name: str) -> nn.Module:
    loader_name = f"load_{model_name}"
    if loader_name not in get_loader_names():
        raise ValueError(f"Model {model_name} not found.")
    loader = eval(loader_name)
    model, preprocess = loader()
    return model, preprocess


def get_availible_models() -> List[str]:
    loader_names = get_loader_names()
    model_names = [n[5:] for n in loader_names]
    return model_names
