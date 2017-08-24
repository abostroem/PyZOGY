from . import util
from .image_class import ImageClass
from astropy.io import fits
import numpy as np

# clobber keyword is deprecated in astropy 1.3
from astropy import __version__
if __version__ < '1.3':
    overwrite = {'clobber': True}
else:
    overwrite = {'overwrite': True}


def calculate_difference_image(science, reference, gain_ratio=np.inf, gain_mask=None, use_pixels=False, show=False):
    """Calculate the difference image using the Zackay algorithm"""

    # match the gains
    if gain_ratio == np.inf:
        if gain_mask is not None:
            gain_mask_data = fits.getdata(gain_mask)
            science.mask[gain_mask_data == 1] = 1
            reference.mask[gain_mask_data == 1] = 1
        if use_pixels:
            min_elements = 800  # pixels in stars
        else:
            min_elements = 20  # stars
        science.zero_point = util.solve_iteratively(science, reference,
                                                    min_elements=min_elements, use_pixels=use_pixels, show=show)
    else:
        science.zero_point = gain_ratio

    # create required arrays
    science_image = science
    reference_image = reference
    science_psf = science.psf
    reference_psf = reference.psf

    # do fourier transforms (fft)
    science_image_fft = np.fft.fft2(science_image)
    reference_image_fft = np.fft.fft2(reference_image)
    science_psf_fft = np.fft.fft2(science_psf)
    reference_psf_fft = np.fft.fft2(reference_psf)

    # calculate difference image
    denominator = science.background_std ** 2 * reference.zero_point ** 2 * abs(reference_psf_fft) ** 2
    denominator += reference.background_std ** 2 * science.zero_point ** 2 * abs(science_psf_fft) ** 2
    difference_image_fft = science_image_fft * reference_psf_fft * reference.zero_point
    difference_image_fft -= reference_image_fft * science_psf_fft * science.zero_point
    difference_image_fft /= np.sqrt(denominator)
    difference_image = np.fft.ifft2(difference_image_fft)

    return difference_image


def calculate_difference_image_zero_point(science, reference):
    """Calculate the flux based zero point of the difference image"""

    denominator = science.background_std ** 2 * reference.zero_point ** 2
    denominator += reference.background_std ** 2 * science.zero_point ** 2
    difference_image_zero_point = science.zero_point * reference.zero_point / np.sqrt(denominator)

    return difference_image_zero_point


def calculate_difference_psf(science, reference, difference_image_zero_point):
    """Calculate the psf of the difference image"""

    science_psf_fft = np.fft.fft2(science.psf)
    reference_psf_fft = np.fft.fft2(reference.psf)
    denominator = science.background_std ** 2 * reference.zero_point ** 2 * abs(reference_psf_fft) ** 2
    denominator += reference.background_std ** 2 * science.zero_point ** 2 * abs(science_psf_fft) ** 2

    difference_psf_fft = science.zero_point * science_psf_fft * reference_psf_fft
    difference_psf_fft /= difference_image_zero_point * np.sqrt(denominator)
    difference_psf = np.fft.ifft2(difference_psf_fft)

    return difference_psf


def calculate_matched_filter_image(difference_image, difference_psf, difference_zero_point):
    """Calculate the matched filter difference image"""

    matched_filter_fft = difference_zero_point * np.fft.fft2(difference_image) * np.conj(np.fft.fft2(difference_psf))
    matched_filter = np.fft.ifft2(matched_filter_fft)
    return matched_filter


def photometric_matched_filter_image(science, reference, matched_filter):
    if (science.variance != np.inf) and (reference.variance != np.inf):
        # add variance correction here
        matched_filter /= 1

    science_psf_fft = np.fft.fft2(science.psf)
    reference_psf_fft = np.fft.fft2(reference.psf)
    zero_point = science.zero_point ** 2 * reference.zero_point ** 2
    zero_point *= abs(science_psf_fft) ** 2 * abs(reference_psf_fft) ** 2
    denominator = reference.background_std ** 2 * science.zero_point ** 2 * abs(science_psf_fft) ** 2
    denominator += science.background_std ** 2 * reference.zero_point ** 2 * abs(reference_psf_fft) ** 2
    zero_point /= denominator
    photometric_matched_filter = matched_filter / np.sum(zero_point)

    return photometric_matched_filter


def normalize_difference_image(difference, difference_image_zero_point, science, reference, normalization='reference'):
    """Normalize to user's choice of image"""

    if normalization == 'reference' or normalization == 't':
        difference_image = difference * reference.zero_point / difference_image_zero_point
    elif normalization == 'science' or normalization == 'i':
        difference_image = difference * science.zero_point / difference_image_zero_point
    else:
        difference_image = difference

    return difference_image


def run_subtraction(science_image, reference_image, science_psf, reference_psf, output='output.fits',
                    science_mask='', reference_mask='', n_stamps=1, normalization='reference',
                    science_saturation=False, reference_saturation=False, science_variance=np.inf,
                    reference_variance=np.inf, matched_filter=None, photometry=True,
                    gain_ratio=np.inf, gain_mask=None, use_pixels=False, show=False):
    """Run full subtraction given filenames and parameters"""

    science = ImageClass(science_image, science_psf, science_mask, n_stamps, science_saturation, gain_mask)
    reference = ImageClass(reference_image, reference_psf, reference_mask, n_stamps, reference_saturation, gain_mask)
    difference = calculate_difference_image(science, reference, gain_ratio, gain_mask, use_pixels, show)
    difference_zero_point = calculate_difference_image_zero_point(science, reference)
    difference_psf = calculate_difference_psf(science, reference, difference_zero_point)
    normalized_difference = normalize_difference_image(difference, difference_zero_point, science, reference, normalization)
    save_difference_image_to_file(normalized_difference, science, normalization, output)
    save_difference_psf_to_file(difference_psf, output.replace('.fits', '.psf.fits'))

    if matched_filter is not None:
        matched_filter_image = calculate_matched_filter_image(difference, difference_psf, difference_zero_point)
        if photometry:
            matched_filter_image = photometric_matched_filter_image(science, reference, matched_filter_image)
        fits.writeto(matched_filter, np.real(matched_filter_image), science.header, output_verify='warn', **overwrite)


def save_difference_image_to_file(difference_image, science, normalization, output):
    """Save difference image to file"""

    hdu = fits.PrimaryHDU(np.real(difference_image))
    hdu.header = science.header.copy()
    hdu.header['PHOTNORM'] = normalization
    hdu.writeto(output, output_verify='warn', **overwrite)


def save_difference_psf_to_file(difference_psf, output):
    """Save difference image psf to file"""
    real_part = np.real(difference_psf)
    center = np.array(real_part.shape) / 2
    centered_psf = np.roll(real_part, center.astype(int), (0, 1))
    fits.writeto(output, centered_psf, output_verify='warn', **overwrite)
