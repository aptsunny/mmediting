# Copyright (c) OpenMMLab. All rights reserved.
import numpy as np
import torch
import torch.distributions as tdist


def random_ccm():
    """Generates random RGB -> Camera color correction matrices."""
    # Takes a random convex combination of XYZ -> Camera CCMs.
    xyz2cams = [[[1.0234, -0.2969, -0.2266], [-0.5625, 1.6328, -0.0469],
                 [-0.0703, 0.2188, 0.6406]],
                [[0.4913, -0.0541, -0.0202], [-0.613, 1.3513, 0.2906],
                 [-0.1564, 0.2151, 0.7183]],
                [[0.838, -0.263, -0.0639], [-0.2887, 1.0725, 0.2496],
                 [-0.0627, 0.1427, 0.5438]],
                [[0.6596, -0.2079, -0.0562], [-0.4782, 1.3016, 0.1933],
                 [-0.097, 0.1581, 0.5181]]]
    num_ccms = len(xyz2cams)
    xyz2cams = torch.FloatTensor(xyz2cams)
    weights = torch.FloatTensor(num_ccms, 1, 1).uniform_(1e-8, 1e8)
    weights_sum = torch.sum(weights, dim=0)
    xyz2cam = torch.sum(xyz2cams * weights, dim=0) / weights_sum

    # Multiplies with RGB -> XYZ to get RGB -> Camera CCM.
    rgb2xyz = torch.FloatTensor([[0.4124564, 0.3575761, 0.1804375],
                                 [0.2126729, 0.7151522, 0.0721750],
                                 [0.0193339, 0.1191920, 0.9503041]])
    rgb2cam = torch.mm(xyz2cam, rgb2xyz)

    # Normalizes each row.
    rgb2cam = rgb2cam / torch.sum(rgb2cam, dim=-1, keepdim=True)
    return rgb2cam


def random_gains(rgb_gain_ratio=1.0,
                 red_gain_range=[1.9, 2.4],
                 blue_gain_range=[1.5, 1.9]):
    """Generates random gains for brightening and white balance."""
    # RGB gain represents brightening.
    n = tdist.Normal(loc=torch.tensor([0.8]), scale=torch.tensor([0.1]))
    rgb_gain = 1.0 / n.sample()
    rgb_gain = rgb_gain_ratio * rgb_gain

    # Red and blue gains represent white balance.
    red_gain = torch.FloatTensor(1).uniform_(red_gain_range[0],
                                             red_gain_range[1])
    blue_gain = torch.FloatTensor(1).uniform_(blue_gain_range[0],
                                              blue_gain_range[1])
    return rgb_gain, red_gain, blue_gain


def inverse_smoothstep(image):
    """Approximately inverts a global tone mapping curve."""
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    image = torch.clamp(image, min=0.0, max=1.0)
    out = 0.5 - torch.sin(torch.asin(1.0 - 2.0 * image) / 3.0)
    out = out.permute(2, 0, 1)  # Re-Permute the tensor back to CxHxW format
    return out


def gamma_expansion(image):
    """Converts from gamma to linear space."""
    # Clamps to prevent numerical instability of gradients near zero.
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    out = torch.clamp(image, min=1e-8)**2.2
    # Re-Permute the tensor back to CxHxW format
    out = out.permute(2, 0, 1)
    return out


def apply_ccm(image, ccm):
    """Applies a color correction matrix."""
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    shape = image.size()
    image = torch.reshape(image, [-1, 3])
    image = torch.tensordot(image, ccm, dims=[[-1], [-1]])
    out = torch.reshape(image, shape)
    out = out.permute(2, 0, 1)  # Re-Permute the tensor back to CxHxW format
    return out


def safe_invert_gains(image, rgb_gain, red_gain, blue_gain):
    """Inverts gains while safely handling saturated pixels."""
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    gains = torch.stack(
        (1.0 / red_gain, torch.tensor([1.0]), 1.0 / blue_gain)) / rgb_gain
    gains = gains.squeeze()
    gains = gains[None, None, :]
    # Prevents dimming of saturated pixels by smoothly masking gains near white
    gray = torch.mean(image, dim=-1, keepdim=True)
    inflection = 0.9
    mask = (torch.clamp(gray - inflection, min=0.0) / (1.0 - inflection))**2.0
    safe_gains = torch.max(mask + (1.0 - mask) * gains, gains)
    out = image * safe_gains
    # Re-Permute the tensor back to CxHxW format
    out = out.permute(2, 0, 1)
    return out


def mosaic(image):
    """Extracts RGGB Bayer planes from an RGB image."""
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    shape = image.size()
    red = image[0::2, 0::2, 0]
    green_red = image[0::2, 1::2, 1]
    green_blue = image[1::2, 0::2, 1]
    blue = image[1::2, 1::2, 2]
    out = torch.stack((red, green_red, green_blue, blue), dim=-1)
    out = torch.reshape(out, (shape[0] // 2, shape[1] // 2, 4))
    # Re-Permute the tensor back to CxHxW format
    out = out.permute(2, 0, 1)
    return out


def random_noise_levels_kpn():
    sigma_read = torch.from_numpy(
        np.power(10, np.random.uniform(-3.0, -1.5, (1, ))))
    # sigma_read = sigma_read**2
    sigma_shot = torch.from_numpy(
        np.power(10, np.random.uniform(-4.0, -2.0, (1, ))))

    sigma_read = sigma_read.type(torch.FloatTensor)
    sigma_shot = sigma_shot.type(torch.FloatTensor)

    return sigma_shot, sigma_read


def add_noise(image,
              shot_noise=0.01,
              read_noise=0.0005,
              read_noise_exponent=2):
    """Adds random shot (proportional to image) and read (independent)
    noise."""
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    variance = image * shot_noise + read_noise**read_noise_exponent
    n = tdist.Normal(
        loc=torch.zeros_like(variance), scale=torch.sqrt(variance))
    noise = n.sample()
    out = image + noise
    out = out.permute(2, 0, 1)  # Re-Permute the tensor back to CxHxW format
    return out


def unprocess(image):
    """Unprocesses an image from sRGB to realistic raw data."""

    # Randomly creates image metadata.
    rgb2cam = random_ccm()
    cam2rgb = torch.inverse(rgb2cam)
    rgb_gain, red_gain, blue_gain = random_gains()

    # Approximately inverts global tone mapping.
    image = inverse_smoothstep(image)
    # Inverts gamma compression.
    image = gamma_expansion(image)
    # Inverts color correction.
    image = apply_ccm(image, rgb2cam)
    # Approximately inverts white balance and brightening.
    image = safe_invert_gains(image, rgb_gain, red_gain, blue_gain)
    # Clips saturated pixels.
    image = torch.clamp(image, min=0.0, max=1.0)
    # Applies a Bayer mosaic.
    image = mosaic(image)

    metadata = {
        'cam2rgb': cam2rgb,
        'rgb2cam': rgb2cam,
        'rgb_gain': rgb_gain,
        'red_gain': red_gain,
        'blue_gain': blue_gain,
    }
    return image, metadata


def unprocess_gt(image):
    """Unprocesses an image from sRGB to realistic raw data."""

    # Randomly creates image metadata.
    rgb2cam = random_ccm()
    cam2rgb = torch.inverse(rgb2cam)
    rgb_gain, red_gain, blue_gain = random_gains()

    # Approximately inverts global tone mapping.
    image = inverse_smoothstep(image)
    # Inverts gamma compression.
    image = gamma_expansion(image)
    # Inverts color correction.
    image = apply_ccm(image, rgb2cam)
    # Approximately inverts white balance and brightening.
    image = safe_invert_gains(image, rgb_gain, red_gain, blue_gain)
    # Clips saturated pixels.
    image = torch.clamp(image, min=0.0, max=1.0)
    # Applies a Bayer mosaic.
    # image = mosaic(image)

    metadata = {
        'cam2rgb': cam2rgb,
        'rgb2cam': rgb2cam,
        'rgb_gain': rgb_gain,
        'red_gain': red_gain,
        'blue_gain': blue_gain,
    }
    return image, metadata


def unprocess_meta_gt(image, rgb_gains, red_gains, blue_gains, rgb2cam,
                      cam2rgb):
    """Unprocesses an image from sRGB to realistic raw data."""

    # Approximately inverts global tone mapping.
    image = inverse_smoothstep(image)
    # Inverts gamma compression.
    image = gamma_expansion(image)
    # Inverts color correction.
    image = apply_ccm(image, rgb2cam)
    # Approximately inverts white balance and brightening.
    image = safe_invert_gains(image, rgb_gains, red_gains, blue_gains)
    # Clips saturated pixels.
    image = torch.clamp(image, min=0.0, max=1.0)
    # Applies a Bayer mosaic.
    # image = mosaic(image)

    metadata = {
        'cam2rgb': cam2rgb,
        'rgb2cam': rgb2cam,
        'rgb_gain': rgb_gains,
        'red_gain': red_gains,
        'blue_gain': blue_gains,
    }
    return image, metadata


def random_noise_levels():
    """Generates random noise levels from a log-log linear distribution."""
    log_min_shot_noise = np.log(0.0001)
    log_max_shot_noise = np.log(0.012)
    log_shot_noise = torch.FloatTensor(1).uniform_(log_min_shot_noise,
                                                   log_max_shot_noise)
    shot_noise = torch.exp(log_shot_noise)

    def line(x):
        return 2.18 * x + 1.20

    n = tdist.Normal(loc=torch.tensor([0.0]), scale=torch.tensor([0.26]))
    log_read_noise = line(log_shot_noise) + n.sample()
    read_noise = torch.exp(log_read_noise)
    return shot_noise, read_noise


def add_noise_test(image, shot_noise=0.01, read_noise=0.0005, count=0):
    """Adds random shot (proportional to image) and read (independent)
    noise."""
    # Permute the image tensor to HxWxC format from CxHxW format
    image = image.permute(1, 2, 0)
    variance = image * shot_noise + read_noise**2
    # n = tdist.Normal(
    #     loc=torch.zeros_like(variance), scale=torch.sqrt(variance))
    # noise  = n.sample()
    seed = torch.Generator()
    seed.manual_seed(count)
    noise = torch.normal(
        mean=torch.zeros_like(variance),
        std=torch.sqrt(variance),
        generator=seed)
    out = image + noise
    # Re-Permute the tensor back to CxHxW format
    out = out.permute(2, 0, 1)
    return out
