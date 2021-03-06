import numpy as np
import numba
import warnings
import scipy.ndimage as scnd
import scipy.optimize as sio
import scipy.signal as scisig
import skimage.feature as skfeat
import matplotlib.colors as mplc
import matplotlib.pyplot as plt
import stemtool as st
import warnings


def angle_fun(
    angle, image_orig, axis=0,
):
    """
    Rotation Sum Finder
    
    Parameters
    ----------
    angle:      float 
                Angle to rotate 
    image_orig: (2,2) shape ndarray
                Input Image
    axis:       int, optional
                Axis along which to perform sum
                     
    Returns
    -------
    rotmin: float
            Sum of the rotated image multiplied by -1 along 
            the axis specified

    Notes
    -----
    This is an internal minimization function for finding the 
    minimum sum of the image at a particular rotation angle.

    See Also
    --------
    rotation_finder 
    """
    rotated_image = scnd.rotate(image_orig, angle, order=5, reshape=False)
    rotsum = (-1) * (np.sum(rotated_image, 1))
    rotmin = np.amin(rotsum)
    return rotmin


def rotation_finder(image_orig, axis=0):
    """
    Angle Finder
    
    Parameters
    ----------
    image_orig: (2,2) shape ndarray
                Input Image
    axis:       int, optional
                Axis along which to perform sum
                     
    Returns
    -------
    min_x: float
           Angle by which if the image is rotated
           by, the sum of the image along the axis
           specified is maximum
    
    Notes
    -----
    Uses the `angle_fun` function as the minimizer.

    See Also
    --------
    angle_fun
    """
    x0 = 90
    x = sio.minimize(angle_fun, x0, args=(image_orig))
    min_x = x.x
    return min_x


def rotate_and_center_ROI(data4D_ROI, rotangle, xcenter, ycenter):
    """
    Rotation Corrector
    
    Parameters
    ----------
    data4D_ROI: ndarray 
                Region of interest of the 4D-STEM dataset in
                the form of ROI pixels (scanning), CBED_Y, CBED_x
    rotangle:   float
                angle in counter-clockwise direction to 
                rotate individual CBED patterns
    xcenter:    float
                X pixel co-ordinate of center of mean pattern
    ycenter:    float
                Y pixel co-ordinate of center of mean pattern
                     
    Returns
    -------
    corrected_ROI: ndarray
                   Each CBED pattern from the region of interest
                   first centered and then rotated along the center
     
    Notes
    -----
    We start by centering each 4D-STEM CBED pattern 
    and then rotating the patterns with respect to the
    pattern center
    """
    data_size = np.asarray(np.shape(data4D_ROI))
    corrected_ROI = np.zeros_like(data4D_ROI)
    for ii in range(data4D_ROI.shape[0]):
        cbed_pattern = data4D_ROI[ii, :, :]
        moved_cbed = np.abs(
            st.util.move_by_phase(
                cbed_pattern,
                (-xcenter + (0.5 * data_size[-1])),
                (-ycenter + (0.5 * data_size[-2])),
            )
        )
        rotated_cbed = scnd.rotate(moved_cbed, rotangle, order=5, reshape=False)
        corrected_ROI[ii, :, :] = rotated_cbed
    return corrected_ROI


def data4Dto2D(data4D):
    """
    Convert 4D data to 2D data
    
    Parameters
    ----------
    data4D: ndarray of shape (4,4)
            the first two dimensions are Fourier
            space, while the next two dimensions
            are real space
                     
    Returns
    -------
    data2D: ndarray of shape (2,2)
            Raveled 2D data where the
            first two dimensions are positions
            while the next two dimensions are spectra
    """
    data2D = np.transpose(data4D, (2, 3, 0, 1))
    data_shape = data2D.shape
    data2D.shape = (data_shape[0] * data_shape[1], data_shape[2] * data_shape[3])
    return data2D


@numba.jit(parallel=True, cache=True)
def resizer1D_numbaopt(data, res, N):
    M = data.size
    carry = 0
    m = 0
    for n in range(int(N)):
        data_sum = carry
        while ((m * N) - (n * M)) < M:
            data_sum += data[m]
            m += 1
        carry = (m - (n + 1) * (M / N)) * data[m - 1]
        data_sum -= carry
        res[n] = data_sum * (N / M)
    return res


@numba.jit(parallel=True, cache=True)
def resizer2D_numbaopt(data2D, resampled_x, resampled_f, sampling):
    data_shape = np.asarray(data2D.shape)
    sampled_shape = (np.round(data_shape / sampling)).astype(int)
    for yy in numba.prange(data_shape[0]):
        resampled_x[yy, :] = resizer1D_numbaopt(
            data2D[yy, :], resampled_x[yy, :], sampled_shape[1]
        )
    for xx in numba.prange(sampled_shape[1]):
        resampled_f[:, xx] = resizer1D_numbaopt(
            resampled_x[:, xx], resampled_f[:, xx], sampled_shape[0]
        )
    return resampled_f


@numba.jit
def bin4D(data4D, bin_factor):
    """
    Bin 4D data in spectral dimensions
    
    Parameters
    ----------
    data4D:     ndarray of shape (4,4)
                the first two dimensions are Fourier
                space, while the next two dimensions
                are real space
    bin_factor: int
                Value by which to bin data
                     
    Returns
    -------
    binned_data: ndarray of shape (4,4)
                 Data binned in the spectral dimensions
    
    Notes
    -----
    The data is binned in the first two dimensions - which are
    the Fourier dimensions using the internal numba functions 
    `resizer2D_numbaopt` and `resizer1D_numbaopt`

    See Also
    --------
    resizer1D_numbaopt
    resizer2D_numbaopt
    """
    data4D_flat = np.reshape(
        data4D, (data4D.shape[0], data4D.shape[1], data4D.shape[2] * data4D.shape[3])
    )
    datashape = np.asarray(data4D_flat.shape)
    res_shape = np.copy(datashape)
    res_shape[0:2] = np.round(datashape[0:2] / bin_factor)
    data4D_res = np.zeros(res_shape.astype(int), dtype=data4D_flat.dtype)
    resampled_x = np.zeros((datashape[0], res_shape[1]), data4D_flat.dtype)
    resampled_f = np.zeros(res_shape[0:2], dtype=data4D_flat.dtype)
    for zz in range(data4D_flat.shape[-1]):
        data4D_res[:, :, zz] = resizer2D_numbaopt(
            data4D_flat[:, :, zz], resampled_x, resampled_f, bin_factor
        )
    binned_data = np.reshape(
        data4D_res,
        (resampled_f.shape[0], resampled_f.shape[1], data4D.shape[2], data4D.shape[3]),
    )
    return binned_data


def test_aperture(pattern, center, radius, showfig=True):
    """
    Test an aperture position for Virtual DF image
    
    Parameters
    ----------
    pattern: ndarray of shape (2,2)
             Diffraction pattern, preferably the
             mean diffraction pattern for testing out
             the aperture location
    center:  ndarray of shape (1,2)
             Center of the circular aperture
    radius:  float
             Radius of the circular aperture
    showfig: bool, optional
             If showfig is True, then the image is
             displayed with the aperture overlaid
                     
    Returns
    -------
    aperture: ndarray of shape (2,2)
              A matrix of the same size of the input image
              with zeros everywhere and ones where the aperture
              is supposed to be
    
    Notes
    -----
    Use the showfig option to visually test out the aperture 
    location with varying parameters
    """
    center = np.asarray(center)
    yy, xx = np.mgrid[0 : pattern.shape[0], 0 : pattern.shape[1]]
    yy = yy - center[1]
    xx = xx - center[0]
    rr = ((yy ** 2) + (xx ** 2)) ** 0.5
    aperture = np.asarray(rr <= radius, dtype=np.double)
    if showfig:
        plt.figure(figsize=(15, 15))
        plt.imshow(st.util.image_normalizer(pattern) + aperture, cmap="Spectral")
        plt.scatter(center[0], center[1], c="w", s=25)
    return aperture


def aperture_image(data4D, center, radius):
    """
    Generate Virtual DF image for a given aperture
    
    Parameters
    ----------
    data4D: ndarray of shape (4,4)
            the first two dimensions are Fourier
            space, while the next two dimensions
            are real space
    center: ndarray of shape (1,2)
            Center of the circular aperture
    radius: float
            Radius of the circular aperture
    
    Returns
    -------
    df_image: ndarray of shape (2,2)
              Generated virtual dark field image
              from the aperture and 4D data
    
    Notes
    -----
    We generate the aperture first, and then make copies
    of the aperture to generate a 4D dataset of the same 
    size as the 4D data. Then we do an element wise 
    multiplication of this aperture 4D data with the 4D data
    and then sum it along the two Fourier directions.
    """
    center = np.array(center)
    yy, xx = np.mgrid[0 : data4D.shape[0], 0 : data4D.shape[1]]
    yy = yy - center[1]
    xx = xx - center[0]
    rr = ((yy ** 2) + (xx ** 2)) ** 0.5
    aperture = np.asarray(rr <= radius, dtype=data4D.dtype)
    apt_copy = np.empty(
        (data4D.shape[2], data4D.shape[3]) + aperture.shape, dtype=data4D.dtype
    )
    apt_copy[:] = aperture
    apt_copy = np.transpose(apt_copy, (2, 3, 0, 1))
    apt_mult = apt_copy * data4D
    df_image = np.sum(np.sum(apt_mult, axis=0), axis=0)
    return df_image


def custom_detector(data4D, det_inner, det_outer, det_center=(0, 0), mrad_calib=0):
    """
    Generate an image with a custom annular detector 
    located anywhere in diffraction space
    
    Parameters
    ----------
    data4D: ndarray of shape (4,4)
            the first two dimensions are Fourier
            space, while the next two dimensions
            are real space
    center: ndarray of shape (1,2)
            Center of the circular aperture
    radius: float
            Radius of the circular aperture
    
    Returns
    -------
    df_image: ndarray of shape (2,2)
              Generated virtual dark field image
              from the aperture and 4D data
    
    Notes
    -----
    We generate the aperture first, and then make copies
    of the aperture to generate a 4D dataset of the same 
    size as the 4D data. Then we do an element wise 
    multiplication of this aperture 4D data with the 4D data
    and then sum it along the two Fourier directions.
    """
    if mrad_calib > 0:
        det_inner = det_inner * mrad_calib
        det_outer = det_outer * mrad_calib
        det_center = np.asarray(det_center) * mrad_calib
    det_center = np.asarray(det_center)
    yy, xx = np.mgrid[0 : data4D.shape[0], 0 : data4D.shape[1]]
    yy -= 0.5 * data4D.shape[0]
    xx -= 0.5 * data4D.shape[1]
    yy = yy - det_center[1]
    xx = xx - det_center[0]
    rr = (yy ** 2) + (xx ** 2)
    aperture = np.logical_and((rr <= det_outer), (rr >= det_inner))
    apt_copy = np.empty(
        (data4D.shape[2], data4D.shape[3]) + aperture.shape, dtype=data4D.dtype
    )
    apt_copy[:] = aperture
    apt_copy = np.transpose(apt_copy, (2, 3, 0, 1))
    apt_mult = apt_copy * data4D
    df_image = np.sum(np.sum(apt_mult, axis=0), axis=0)
    return df_image


def ROI_from_image(image, med_val, style="over", showfig=True):
    if style == "over":
        ROI = np.asarray(image > (med_val * np.median(image)), dtype=np.double)
    else:
        ROI = np.asarray(image < (med_val * np.median(image)), dtype=np.double)
    if showfig:
        plt.figure(figsize=(15, 15))
        plt.imshow(ROI + st.util.image_normalizer(image), cmap="viridis")
        plt.title("ROI overlaid")
    ROI = ROI.astype(bool)
    return ROI


@numba.jit
def colored_mcr(conc_data, data_shape):
    no_spectra = np.shape(conc_data)[1]
    color_hues = np.arange(no_spectra, dtype=np.float64) / no_spectra
    norm_conc = (conc_data - np.amin(conc_data)) / (
        np.amax(conc_data) - np.amin(conc_data)
    )
    saturation_matrix = np.ones(data_shape, dtype=np.float64)
    hsv_calc = np.zeros((data_shape[0], data_shape[1], 3), dtype=np.float64)
    rgb_calc = np.zeros((data_shape[0], data_shape[1], 3), dtype=np.float64)
    hsv_calc[:, :, 1] = saturation_matrix
    for ii in range(no_spectra):
        conc_image = (np.reshape(norm_conc[:, ii], data_shape)).astype(np.float64)
        hsv_calc[:, :, 0] = saturation_matrix * color_hues[ii]
        hsv_calc[:, :, 2] = conc_image
        rgb_calc = rgb_calc + mplc.hsv_to_rgb(hsv_calc)
    rgb_image = rgb_calc / np.amax(rgb_calc)
    return rgb_image


@numba.jit
def fit_nbed_disks(corr_image, disk_size, positions, diff_spots, nan_cutoff=0):
    """
    Disk Fitting algorithm for a single NBED pattern
    
    Parameters
    ----------
    corr_image: ndarray of shape (2,2)
                The cross-correlated image of the NBED that 
                will be fitted
    disk_size:  float
                Size of each NBED disks in pixels
    positions:  ndarray of shape (n,2)
                X and Y positions where n is the number of positions.
                These are the initial guesses that will be refined
    diff_spots: ndarray of shape (n,2)
                a and b Miller indices corresponding to the
                disk positions
    nan_cutoff: float, optional
                Optional parameter that is used for thresholding disk
                fits. If the intensity ratio is below the threshold 
                the position will not be fit. Default value is 0
    
    Returns
    -------
    fitted_disk_list: ndarray of shape (n,2)
                      Sub-pixel precision Gaussian fitted disk
                      locations. If nan_cutoff is greater than zero, then
                      only the positions that are greater than the threshold 
                      are returned.
    center_position:  ndarray of shape (1,2)
                      Location of the central (0,0) disk
    fit_deviation:    ndarray of shape (1,2)
                      Standard deviation of the X and Y disk fits given as pixel 
                      ratios
    lcbed:            ndarray of shape (2,2)
                      Matrix defining the Miller indices axes
    
    Notes
    -----
    Every disk position is fitted with a 2D Gaussian by cutting off a circle
    of the size of disk_size around the initial poistions. If nan-cutoff is above 
    zero then only the locations inside this cutoff where the maximum pixel intensity 
    is (1+nan_cutoff) times the median pixel intensity will be fitted. Use this 
    parameter carefully, because in some cases this may result in no disks being fitted
    and the program throwing weird errors at you. 
    """
    warnings.filterwarnings("ignore")
    no_pos = int(np.shape(positions)[0])
    diff_spots = np.asarray(diff_spots, dtype=np.float64)
    fitted_disk_list = np.zeros_like(positions)
    yy, xx = np.mgrid[0 : (corr_image.shape[0]), 0 : (corr_image.shape[1])]
    for ii in range(no_pos):
        posx = positions[ii, 0]
        posy = positions[ii, 1]
        reg = ((yy - posy) ** 2) + ((xx - posx) ** 2) <= (disk_size ** 2)
        peak_ratio = np.amax(corr_image[reg]) / np.median(corr_image[reg])
        if peak_ratio < (1 + nan_cutoff):
            fitted_disk_list[ii, 0:2] = np.nan
        else:
            par = st.util.fit_gaussian2D_mask(corr_image, posx, posy, disk_size)
            fitted_disk_list[ii, 0:2] = par[0:2]
    nancount = np.int(np.sum(np.isnan(fitted_disk_list)) / 2)
    if nancount == no_pos:
        center_position = np.nan * np.ones((1, 2))
        fit_deviation = np.nan
        lcbed = np.nan
    else:
        diff_spots = (diff_spots[~np.isnan(fitted_disk_list)]).reshape(
            (no_pos - nancount), 2
        )
        fitted_disk_list = (fitted_disk_list[~np.isnan(fitted_disk_list)]).reshape(
            (no_pos - nancount), 2
        )
        disk_locations = np.copy(fitted_disk_list)
        disk_locations[:, 1] = (-1) * disk_locations[:, 1]
        center = disk_locations[
            np.logical_and((diff_spots[:, 0] == 0), (diff_spots[:, 1] == 0)), :
        ]
        if center.shape[0] > 0:
            cx = center[0, 0]
            cy = center[0, 1]
            center_position = np.asarray((cx, -cy), dtype=np.float64)
            if (nancount / no_pos) < 0.5:
                disk_locations[:, 0:2] = disk_locations[:, 0:2] - np.asarray(
                    (cx, cy), dtype=np.float64
                )
                lcbed, _, _, _ = np.linalg.lstsq(diff_spots, disk_locations, rcond=None)
                calc_points = np.matmul(diff_spots, lcbed)
                stdx = np.std(
                    np.divide(
                        disk_locations[np.where(calc_points[:, 0] != 0), 0],
                        calc_points[np.where(calc_points[:, 0] != 0), 0],
                    )
                )
                stdy = np.std(
                    np.divide(
                        disk_locations[np.where(calc_points[:, 1] != 0), 1],
                        calc_points[np.where(calc_points[:, 1] != 0), 1],
                    )
                )
                fit_deviation = np.asarray((stdx, stdy), dtype=np.float64)
            else:
                fit_deviation = np.nan
                lcbed = np.nan
        else:
            center_position = np.nan
            fit_deviation = np.nan
            lcbed = np.nan
    return fitted_disk_list, center_position, fit_deviation, lcbed


@numba.jit
def strain_in_ROI(
    data4D,
    ROI,
    center_disk,
    disk_list,
    pos_list,
    reference_axes=0,
    med_factor=10,
    gauss_val=3,
    hybrid_cc=0.1,
    nan_cutoff=0.5,
):
    """
    Get strain from a region of interest
    
    Parameters
    ----------
    data4D:         ndarray
                    This is a 4D dataset where the first two dimensions
                    are the diffraction dimensions and the next two 
                    dimensions are the scan dimensions
    ROI:            ndarray of dtype bool
                    Region of interest
    center_disk:    ndarray
                    The blank diffraction disk template where
                    it is 1 inside the circle and 0 outside
    disk_list:      ndarray of shape (n,2)
                    X and Y positions where n is the number of positions.
                    These are the initial guesses that will be refined
    pos_list:       ndarray of shape (n,2)
                    a and b Miller indices corresponding to the
                    disk positions
    reference_axes: ndarray, optional
                    The unit cell axes from the reference region. Strain is
                    calculated by comapring the axes at a scan position with 
                    the reference axes values. If it is 0, then the average 
                    NBED axes will be calculated and will be used as the 
                    reference axes.
    med_factor:     float, optional
                    Due to detector noise, some stray pixels may often be brighter 
                    than the background. This is used for damping any such pixels.
                    Default is 30
    gauss_val:      float, optional
                    The standard deviation of the Gaussian filter applied to the
                    logarithm of the CBED pattern. Default is 3
    hybrid_cc:      float, optional
                    Hybridization parameter to be used for cross-correlation.
                    Default is 0.1
    nan_cutoff:     float, optional
                    Parameter that is used for thresholding disk
                    fits. If the intensity ratio is below the threshold 
                    the position will not be fit. Default value is 0.5    
    
    Returns
    -------
    e_xx_map: ndarray
              Strain in the xx direction in the region of interest
    e_xy_map: ndarray
              Strain in the xy direction in the region of interest
    e_th_map: ndarray
              Angular strain in the region of interest
    e_yy_map: ndarray
              Strain in the yy direction in the region of interest
    fit_std:  ndarray
              x and y deviations in axes fitting for the scan points
    
    Notes
    -----
    At every scan position, the diffraction disk is filtered by first taking
    the log of the CBED pattern, and then by applying a Gaussian filter. 
    Following this the Sobel of the filtered dataset is calculated. 
    The intensity of the Sobel, Gaussian and Log filtered CBED data is then
    inspected for outlier pixels. If pixel intensities are higher or lower than
    a threshold of the median pixel intensity, they are replaced by the threshold
    value. This is then hybrid cross-correlated with the Sobel magnitude of the 
    template disk. If the pattern axes return a numerical value, then the strain
    is calculated for that scan position, else it is NaN
    """
    warnings.filterwarnings("ignore")
    # Calculate needed values
    scan_y, scan_x = np.mgrid[0 : data4D.shape[2], 0 : data4D.shape[3]]
    data4D_ROI = data4D[:, :, scan_y[ROI], scan_x[ROI]]
    no_of_disks = data4D_ROI.shape[-1]
    disk_size = (np.sum(st.util.image_normalizer(center_disk)) / np.pi) ** 0.5
    i_matrix = (np.eye(2)).astype(np.float64)
    sobel_center_disk, _ = st.util.sobel(center_disk)
    # Initialize matrices
    e_xx_ROI = np.nan * (np.ones(no_of_disks, dtype=np.float64))
    e_xy_ROI = np.nan * (np.ones(no_of_disks, dtype=np.float64))
    e_th_ROI = np.nan * (np.ones(no_of_disks, dtype=np.float64))
    e_yy_ROI = np.nan * (np.ones(no_of_disks, dtype=np.float64))
    fit_std = np.nan * (np.ones((no_of_disks, 2), dtype=np.float64))
    e_xx_map = np.nan * np.ones_like(scan_y)
    e_xy_map = np.nan * np.ones_like(scan_y)
    e_th_map = np.nan * np.ones_like(scan_y)
    e_yy_map = np.nan * np.ones_like(scan_y)
    # Calculate for mean CBED if no reference
    # axes present
    if np.size(reference_axes) < 2:
        mean_cbed = np.mean(data4D_ROI, axis=-1)
        sobel_lm_cbed, _ = st.util.sobel(st.util.image_logarizer(mean_cbed))
        sobel_lm_cbed[
            sobel_lm_cbed > med_factor * np.median(sobel_lm_cbed)
        ] = np.median(sobel_lm_cbed)
        lsc_mean = st.util.cross_corr(
            sobel_lm_cbed, sobel_center_disk, hybridizer=hybrid_cc
        )
        _, _, _, mean_axes = fit_nbed_disks(lsc_mean, disk_size, disk_list, pos_list)
        inverse_axes = np.linalg.inv(mean_axes)
    else:
        inverse_axes = np.linalg.inv(reference_axes)
    for ii in range(int(no_of_disks)):
        pattern = data4D_ROI[:, :, ii]
        sobel_log_pattern, _ = st.util.sobel(
            scnd.gaussian_filter(st.util.image_logarizer(pattern), gauss_val)
        )
        sobel_log_pattern[
            sobel_log_pattern > med_factor * np.median(sobel_log_pattern)
        ] = (np.median(sobel_log_pattern) * med_factor)
        sobel_log_pattern[
            sobel_log_pattern < np.median(sobel_log_pattern) / med_factor
        ] = (np.median(sobel_log_pattern) / med_factor)
        lsc_pattern = st.util.cross_corr(
            sobel_log_pattern, sobel_center_disk, hybridizer=hybrid_cc
        )
        _, _, std, pattern_axes = fit_nbed_disks(
            lsc_pattern, disk_size, disk_list, pos_list, nan_cutoff
        )
        if ~(np.isnan(np.ravel(pattern_axes))[0]):
            fit_std[ii, :] = std
            t_pattern = np.matmul(pattern_axes, inverse_axes)
            s_pattern = t_pattern - i_matrix
            e_xx_ROI[ii] = -s_pattern[0, 0]
            e_xy_ROI[ii] = -(s_pattern[0, 1] + s_pattern[1, 0])
            e_th_ROI[ii] = s_pattern[0, 1] - s_pattern[1, 0]
            e_yy_ROI[ii] = -s_pattern[1, 1]
    e_xx_map[ROI] = e_xx_ROI
    e_xx_map[np.isnan(e_xx_map)] = 0
    e_xx_map = scnd.gaussian_filter(e_xx_map, 1)
    e_xy_map[ROI] = e_xy_ROI
    e_xy_map[np.isnan(e_xy_map)] = 0
    e_xy_map = scnd.gaussian_filter(e_xy_map, 1)
    e_th_map[ROI] = e_th_ROI
    e_th_map[np.isnan(e_th_map)] = 0
    e_th_map = scnd.gaussian_filter(e_th_map, 1)
    e_yy_map[ROI] = e_yy_ROI
    e_yy_map[np.isnan(e_yy_map)] = 0
    e_yy_map = scnd.gaussian_filter(e_yy_map, 1)
    return e_xx_map, e_xy_map, e_th_map, e_yy_map, fit_std


@numba.jit
def strain_log(
    data4D_ROI, center_disk, disk_list, pos_list, reference_axes=0, med_factor=10
):
    warnings.filterwarnings("ignore")
    # Calculate needed values
    no_of_disks = data4D_ROI.shape[-1]
    disk_size = (np.sum(center_disk) / np.pi) ** 0.5
    i_matrix = (np.eye(2)).astype(np.float64)
    # Initialize matrices
    e_xx_log = np.zeros(no_of_disks, dtype=np.float64)
    e_xy_log = np.zeros(no_of_disks, dtype=np.float64)
    e_th_log = np.zeros(no_of_disks, dtype=np.float64)
    e_yy_log = np.zeros(no_of_disks, dtype=np.float64)
    # Calculate for mean CBED if no reference
    # axes present
    if np.size(reference_axes) < 2:
        mean_cbed = np.mean(data4D_ROI, axis=-1)
        log_cbed, _ = st.util.image_logarizer(mean_cbed)
        log_cc_mean = st.util.cross_corr(log_cbed, center_disk, hybridizer=0.1)
        _, _, mean_axes = fit_nbed_disks(log_cc_mean, disk_size, disk_list, pos_list)
        inverse_axes = np.linalg.inv(mean_axes)
    else:
        inverse_axes = np.linalg.inv(reference_axes)
    for ii in range(int(no_of_disks)):
        pattern = data4D_ROI[:, :, ii]
        log_pattern, _ = st.util.image_logarizer(pattern)
        log_cc_pattern = st.util.cross_corr(log_pattern, center_disk, hybridizer=0.1)
        _, _, pattern_axes = fit_nbed_disks(
            log_cc_pattern, disk_size, disk_list, pos_list
        )
        t_pattern = np.matmul(pattern_axes, inverse_axes)
        s_pattern = t_pattern - i_matrix
        e_xx_log[ii] = -s_pattern[0, 0]
        e_xy_log[ii] = -(s_pattern[0, 1] + s_pattern[1, 0])
        e_th_log[ii] = s_pattern[0, 1] - s_pattern[1, 0]
        e_yy_log[ii] = -s_pattern[1, 1]
    return e_xx_log, e_xy_log, e_th_log, e_yy_log


@numba.jit
def strain_oldstyle(data4D_ROI, center_disk, disk_list, pos_list, reference_axes=0):
    warnings.filterwarnings("ignore")
    # Calculate needed values
    no_of_disks = data4D_ROI.shape[-1]
    disk_size = (np.sum(center_disk) / np.pi) ** 0.5
    i_matrix = (np.eye(2)).astype(np.float64)
    # Initialize matrices
    e_xx_ROI = np.zeros(no_of_disks, dtype=np.float64)
    e_xy_ROI = np.zeros(no_of_disks, dtype=np.float64)
    e_th_ROI = np.zeros(no_of_disks, dtype=np.float64)
    e_yy_ROI = np.zeros(no_of_disks, dtype=np.float64)
    # Calculate for mean CBED if no reference
    # axes present
    if np.size(reference_axes) < 2:
        mean_cbed = np.mean(data4D_ROI, axis=-1)
        cc_mean = st.util.cross_corr(mean_cbed, center_disk, hybridizer=0.1)
        _, _, mean_axes = fit_nbed_disks(cc_mean, disk_size, disk_list, pos_list)
        inverse_axes = np.linalg.inv(mean_axes)
    else:
        inverse_axes = np.linalg.inv(reference_axes)
    for ii in range(int(no_of_disks)):
        pattern = data4D_ROI[:, :, ii]
        cc_pattern = st.util.cross_corr(pattern, center_disk, hybridizer=0.1)
        _, _, pattern_axes = fit_nbed_disks(cc_pattern, disk_size, disk_list, pos_list)
        t_pattern = np.matmul(pattern_axes, inverse_axes)
        s_pattern = t_pattern - i_matrix
        e_xx_ROI[ii] = -s_pattern[0, 0]
        e_xy_ROI[ii] = -(s_pattern[0, 1] + s_pattern[1, 0])
        e_th_ROI[ii] = s_pattern[0, 1] - s_pattern[1, 0]
        e_yy_ROI[ii] = -s_pattern[1, 1]
    return e_xx_ROI, e_xy_ROI, e_th_ROI, e_yy_ROI


def ROI_strain_map(strain_ROI, ROI):
    """
    Convert the strain in the ROI array to a strain map
    """
    strain_map = np.zeros_like(ROI, dtype=np.float64)
    strain_map[ROI] = (strain_ROI).astype(np.float64)
    return strain_map


@numba.jit(cache=True, parallel=True)
def log_sobel4D(data4D, scan_dims, med_factor=30, gauss_val=3):
    """
    Take the Log-Sobel of a pattern. 
    
    Parameters
    ----------
    data4D:     ndarray 
                4D dataset whose CBED patterns will be filtered
    scan_dims:  tuple
                Scan dimensions. If your scanning pixels are for 
                example the first two dimensions specify it as (0,1)
                Will be converted to numpy array so pass tuple only
    med_factor: float, optional
                Due to detector noise, some stray pixels may often 
                be brighter than the background. This is used for 
                damping any such pixels. Default is 30
    gauss_val:  float, optional
                The standard deviation of the Gaussian filter applied 
                to the logarithm of the CBED pattern. Default is 3
    
    Returns
    -------
    data_lsb: ndarray
              4D dataset where each CBED pattern has been log
              Sobel filtered
    
    Notes
    -----
    Generate the Sobel filtered pattern of the logarithm of
    a dataset. Compared to running the Sobel filter back on
    a log dataset, this takes care of somethings - notably
    a Gaussian blur is applied to the image, and Sobel spikes
    are removed when any values are too higher or lower than 
    the median of the image. This is because real detector
    images often are very noisy. This code generates the filtered
    CBED at every scan position, and is dimension agnostic, in
    that your CBED dimensions can either be the first two or last
    two - just specify the dimensions. Also if loops weirdly need
    to be outside the for loops - this is a numba feature (bug?)
    Small change - made the Sobel matrix order 5 rather than 3
    
    See Also
    --------
    dpc.log_sobel
    """
    scan_dims = np.asarray(scan_dims)
    scan_dims[scan_dims < 0] = 4 + scan_dims[scan_dims < 0]
    sum_dims = np.sum(scan_dims)
    if sum_dims < 2:
        data4D = np.transpose(data4D, (2, 3, 0, 1))
    data_lsb = np.zeros_like(data4D, dtype=np.float)
    for jj in numba.prange(data4D.shape[int(scan_dims[1])]):
        for ii in range(data4D.shape[int(scan_dims[0])]):
            pattern = data4D[:, :, ii, jj]
            pattern = 1000 * (1 + st.util.image_normalizer(pattern))
            lsb_pattern, _ = st.util.sobel(
                scnd.gaussian_filter(st.util.image_logarizer(pattern), gauss_val), 5
            )
            lsb_pattern[lsb_pattern > med_factor * np.median(lsb_pattern)] = (
                np.median(lsb_pattern) * med_factor
            )
            lsb_pattern[lsb_pattern < np.median(lsb_pattern) / med_factor] = (
                np.median(lsb_pattern) / med_factor
            )
            data_lsb[:, :, ii, jj] = lsb_pattern
    if sum_dims < 2:
        data_lsb = np.transpose(data_lsb, (2, 3, 0, 1))
    return data_lsb


def spectra_finder(data4D, yvals, xvals):
    spectra_data = np.ravel(
        np.mean(
            data4D[:, :, yvals[0] : yvals[1], xvals[0] : xvals[1]],
            axis=(-1, -2),
            dtype=np.float64,
        )
    )
    data_im = np.sum(data4D, axis=(0, 1))
    data_im = (data_im - np.amin(data_im)) / (np.amax(data_im) - np.amin(data_im))
    overlay = np.zeros_like(data_im)
    overlay[yvals[0] : yvals[1], xvals[0] : xvals[1]] = 1
    return spectra_data, 0.5 * (data_im + overlay)


def sort_edges(edge_map, edge_distance=5):
    yV, xV = np.mgrid[0 : np.shape(edge_map)[0], 0 : np.shape(edge_map)[1]]
    dist_points = np.zeros_like(yV)
    yy = yV[edge_map]
    xx = xV[edge_map]
    no_points = np.size(yy)
    points = np.arange(no_points)
    point_list = np.transpose(np.asarray((yV[edge_map], xV[edge_map])))
    truth_list = np.zeros((no_points, 2), dtype=bool)
    edge_list_1 = np.zeros((no_points, 2))
    point_number = 0
    edge_list_1[int(point_number), 0:2] = np.asarray((yy[0], xx[0]))
    truth_list[int(point_number), 0:2] = True
    edge_points = 1
    for ii in np.arange(no_points):
        last_yy = edge_list_1[int(edge_points - 1), 0]
        last_xx = edge_list_1[int(edge_points - 1), 1]
        other_points = np.reshape(
            point_list[~truth_list], (int(no_points - edge_points), 2)
        )
        dist_vals = (
            ((other_points[:, 0] - last_yy) ** 2)
            + ((other_points[:, 1] - last_xx) ** 2)
        ) ** 0.5
        min_dist = np.amin(dist_vals)
        if min_dist < edge_distance:
            n_yy = other_points[dist_vals == min_dist, 0][0]
            n_xx = other_points[dist_vals == min_dist, 1][0]
            point_number = points[
                (point_list[:, 0] == n_yy) & (point_list[:, 1] == n_xx)
            ][0]
            edge_list_1[int(edge_points), 0:2] = np.asarray((n_yy, n_xx))
            truth_list[int(point_number), 0:2] = True
            edge_points = edge_points + 1.0
    list_1 = np.reshape(point_list[truth_list], (int(edge_points), 2))
    list_2 = np.reshape(point_list[~truth_list], (int(no_points - edge_points), 2))
    edge1 = np.zeros_like(edge_map)
    edge1[list_1[:, 0], list_1[:, 1]] = 1
    edge2 = np.zeros_like(edge_map)
    edge2[list_2[:, 0], list_2[:, 1]] = 1
    edge1_sum = np.sum(edge1)
    edge2_sum = np.sum(edge2)
    if edge1_sum > edge2_sum:
        outer_edge = np.copy(edge1)
        inner_edge = np.copy(edge2)
    else:
        outer_edge = np.copy(edge2)
        inner_edge = np.copy(edge1)
    return outer_edge, inner_edge


@numba.jit
def get_inside(edges, cutoff=0.95):
    big_size = (2.5 * np.asarray(edges.shape)).astype(int)
    starter = (0.5 * (big_size - np.asarray(edges.shape))).astype(int)
    bigger_aa = np.zeros(big_size)
    bigger_aa[
        starter[0] : starter[0] + edges.shape[0],
        starter[1] : starter[1] + edges.shape[1],
    ] = edges
    aa1 = bigger_aa.astype(bool)
    aa2 = (np.fliplr(bigger_aa)).astype(bool)
    yy, xx = np.mgrid[0 : big_size[0], 0 : big_size[1]]
    positions = np.zeros((bigger_aa.size, 2), dtype=int)
    positions[:, 0] = np.ravel(yy)
    positions[:, 1] = np.ravel(xx)
    yy_aa1 = yy[aa1]
    xx_aa1 = xx[aa1]
    yy_aa2 = yy[aa2]
    xx_aa2 = xx[aa2]
    ang_range1 = np.zeros_like(yy, dtype=np.float)
    ang_range2 = np.zeros_like(yy, dtype=np.float)
    for ii in numba.prange(len(positions)):
        angles1 = (180 / np.pi) * np.arctan2(
            yy_aa1 - positions[ii, 0], xx_aa1 - positions[ii, 1]
        )
        ang_range1[positions[ii, 0], positions[ii, 1]] = np.amax(angles1) - np.amin(
            angles1
        )
    for jj in numba.prange(len(positions)):
        angles2 = (180 / np.pi) * np.arctan2(
            yy_aa2 - positions[jj, 0], xx_aa2 - positions[jj, 1]
        )
        ang_range2[positions[jj, 0], positions[jj, 1]] = np.amax(angles2) - np.amin(
            angles2
        )
    ang_range2 = np.fliplr(ang_range2)
    ang_range = np.logical_and(
        ang_range1 > cutoff * np.amax(ang_range1),
        ang_range2 > cutoff * np.amax(ang_range2),
    )
    real_ang_range = np.zeros_like(edges, dtype=bool)
    real_ang_range = ang_range[
        starter[0] : starter[0] + edges.shape[0],
        starter[1] : starter[1] + edges.shape[1],
    ]
    return real_ang_range


def sobel_filter(image, med_filter=50):
    ls_image, _ = st.util.sobel(st.util.image_logarizer(image))
    ls_image[ls_image > (med_filter * np.median(ls_image))] = med_filter * np.median(
        ls_image
    )
    ls_image[ls_image < (np.median(ls_image) / med_filter)] = (
        np.median(ls_image) / med_filter
    )
    return ls_image


@numba.jit
def strain4D_general(
    data4D,
    disk_radius,
    ROI=0,
    disk_center=np.nan,
    rotangle=0,
    med_factor=30,
    gauss_val=3,
    hybrid_cc=0.2,
):
    """
    Get strain from a ROI without the need for
    specifying Miller indices of diffraction spots
    
    Parameters
    ----------
    data4D:      ndarray
                 This is a 4D dataset where the first two dimensions
                 are the diffraction dimensions and the next two 
                 dimensions are the scan dimensions
    disk_radius: float
                 Radius in pixels of the diffraction disks
    ROI:         ndarray, optional
                 Region of interest. If no ROI is passed then the entire
                 scan region is the ROI
    disk_center: tuple, optional
                 Location of the center of the diffraction disk - closest to
                 the <000> undiffracted beam
    rotangle:    float, optional
                 Angle of rotation of the CBED with respect to the optic axis
                 This must be in degrees
    med_factor:  float, optional
                 Due to detector noise, some stray pixels may often be brighter 
                 than the background. This is used for damping any such pixels.
                 Default is 30
    gauss_val:   float, optional
                 The standard deviation of the Gaussian filter applied to the
                 logarithm of the CBED pattern. Default is 3
    hybrid_cc:   float, optional
                 Hybridization parameter to be used for cross-correlation.
                 Default is 0.1  
    
    Returns
    -------
    e_xx_map: ndarray
              Strain in the xx direction in the region of interest
    e_xy_map: ndarray
              Strain in the xy direction in the region of interest
    e_th_map: ndarray
              Angular strain in the region of interest
    e_yy_map: ndarray
              Strain in the yy direction in the region of interest
    list_pos: ndarray
              List of all the higher order peak positions with 
              respect to the central disk for all positions in the ROI
    
    Notes
    -----
    We first of all calculate the preconditioned data (log + Sobel filtered)
    for every CBED pattern in the ROI. Then the mean preconditioned 
    pattern is calculated and cross-correlated with the Sobel template. The disk 
    positions are as peaks in this cross-correlated pattern, with the central
    disk the one closest to the center of the CBED pattern. Using that insight
    the distances of the higher order diffraction disks are calculated with respect
    to the central transmitted beam. This is then performed for all other CBED 
    patterns. The calculated higher order disk locations are then compared to the 
    higher order disk locations for the median pattern to generate strain maps.
    """
    rotangle = np.deg2rad(rotangle)
    rotmatrix = np.asarray(
        ((np.cos(rotangle), -np.sin(rotangle)), (np.sin(rotangle), np.cos(rotangle)))
    )
    diff_y, diff_x = np.mgrid[0 : data4D.shape[0], 0 : data4D.shape[1]]
    if np.isnan(np.mean(disk_center)):
        disk_center = np.asarray(np.shape(diff_y)) / 2
    else:
        disk_center = np.asarray(disk_center)
    e_xx_map = np.nan * np.ones((data4D.shape[2], data4D.shape[3]))
    e_xy_map = np.nan * np.ones((data4D.shape[2], data4D.shape[3]))
    e_th_map = np.nan * np.ones((data4D.shape[2], data4D.shape[3]))
    e_yy_map = np.nan * np.ones((data4D.shape[2], data4D.shape[3]))
    radiating = ((diff_y - disk_center[0]) ** 2) + ((diff_x - disk_center[1]) ** 2)
    disk = np.zeros_like(radiating)
    disk[radiating < (disk_radius ** 2)] = 1
    sobel_disk, _ = st.util.sobel(disk)
    if np.sum(ROI) == 0:
        imROI = np.ones_like(e_xx_map, dtype=bool)
    else:
        imROI = ROI
    ROI_4D = data4D[:, :, imROI]
    no_of_disks = ROI_4D.shape[-1]
    LSB_ROI = np.zeros_like(ROI_4D, dtype=np.float)
    for ii in range(no_of_disks):
        cbed = ROI_4D[:, :, ii]
        cbed = 1000 * (1 + st.util.image_normalizer(cbed))
        lsb_cbed, _ = st.util.sobel(
            scnd.gaussian_filter(st.util.image_logarizer(cbed), gauss_val)
        )
        lsb_cbed[lsb_cbed > med_factor * np.median(lsb_cbed)] = (
            np.median(lsb_cbed) * med_factor
        )
        lsb_cbed[lsb_cbed < np.median(lsb_cbed) / med_factor] = (
            np.median(lsb_cbed) / med_factor
        )
        LSB_ROI[:, :, ii] = lsb_cbed
    Mean_LSB = np.median(LSB_ROI, axis=(-1))
    LSB_CC = st.util.cross_corr(Mean_LSB, sobel_disk, hybrid_cc)
    data_peaks = skfeat.peak_local_max(
        LSB_CC, min_distance=int(2 * disk_radius), indices=False
    )
    peak_labels = scnd.measurements.label(data_peaks)[0]
    merged_peaks = np.asarray(
        scnd.measurements.center_of_mass(
            data_peaks, peak_labels, range(1, np.max(peak_labels) + 1)
        )
    )
    fitted_mean = np.zeros_like(merged_peaks, dtype=np.float64)
    fitted_scan = np.zeros_like(merged_peaks, dtype=np.float64)
    for jj in range(merged_peaks.shape[0]):
        par = st.util.fit_gaussian2D_mask(
            LSB_CC, merged_peaks[jj, 1], merged_peaks[jj, 0], disk_radius
        )
        fitted_mean[jj, 0:2] = np.flip(par[0:2])
    distarr = (
        np.sum(((fitted_mean - np.asarray(LSB_CC.shape) / 2) ** 2), axis=1)
    ) ** 0.5
    peaks_mean = (
        fitted_mean[distarr != np.amin(distarr), :]
        - fitted_mean[distarr == np.amin(distarr), :]
    )
    list_pos = np.zeros((int(np.sum(imROI)), peaks_mean.shape[0], peaks_mean.shape[1]))
    exx_ROI = np.ones(no_of_disks, dtype=np.float64)
    exy_ROI = np.ones(no_of_disks, dtype=np.float64)
    eth_ROI = np.ones(no_of_disks, dtype=np.float64)
    eyy_ROI = np.ones(no_of_disks, dtype=np.float64)
    for kk in range(no_of_disks):
        scan_LSB = LSB_ROI[:, :, kk]
        scan_CC = st.util.cross_corr(scan_LSB, sobel_disk, hybrid_cc)
        for qq in range(merged_peaks.shape[0]):
            scan_par = st.util.fit_gaussian2D_mask(
                scan_CC, fitted_mean[qq, 1], fitted_mean[qq, 0], disk_radius
            )
            fitted_scan[qq, 0:2] = np.flip(scan_par[0:2])
        peaks_scan = (
            fitted_scan[distarr != np.amin(distarr), :]
            - fitted_scan[distarr == np.amin(distarr), :]
        )
        list_pos[kk, :, :] = peaks_scan
        scan_strain, _, _, _ = np.linalg.lstsq(peaks_mean, peaks_scan, rcond=None)
        scan_strain = np.matmul(scan_strain, rotmatrix)
        scan_strain = scan_strain - np.eye(2)
        exx_ROI[kk] = scan_strain[0, 0]
        exy_ROI[kk] = (scan_strain[0, 1] + scan_strain[1, 0]) / 2
        eth_ROI[kk] = (scan_strain[0, 1] - scan_strain[1, 0]) / 2
        eyy_ROI[kk] = scan_strain[1, 1]
    e_xx_map[imROI] = exx_ROI
    e_xx_map[np.isnan(e_xx_map)] = 0
    e_xx_map = scnd.gaussian_filter(e_xx_map, 1)
    e_xy_map[imROI] = exy_ROI
    e_xy_map[np.isnan(e_xy_map)] = 0
    e_xy_map = scnd.gaussian_filter(e_xy_map, 1)
    e_th_map[imROI] = eth_ROI
    e_th_map[np.isnan(e_th_map)] = 0
    e_th_map = scnd.gaussian_filter(e_th_map, 1)
    e_yy_map[imROI] = eyy_ROI
    e_yy_map[np.isnan(e_yy_map)] = 0
    e_yy_map = scnd.gaussian_filter(e_yy_map, 1)
    return e_xx_map, e_xy_map, e_th_map, e_yy_map, list_pos


def bin_scan(data4D, bin_factor):
    """
    Bin the data in the scan dimensions
     
    Parameters
    ----------
    data4D:     ndarray
                This is a 4D dataset where the first two dimensions
                are the dffraction dimensions and the next two 
                dimensions are the scan dimensions
    bin_factor: int or tuple
                Binning factor for scan dimensions
    
    Returns
    -------
    binned_4D: ndarray
               The data binned in the scanned dimensions.
     
    Notes
    -----
    You can specify the bin factor to be either an integer or
    a tuple. If you specify an integer the same binning will 
    be used in both the scan X and scan Y dimensions, while if
    you specify a tuple then different binning factors for each 
    dimensions.
    
    Examples
    --------
    Run as:
    
    >>> binned_4D = bin_scan(data4D, 4)
    
    This will bin the scan dimensions by 4. This is functionally
    identical to:
    
    >>> binned_4D = bin_scan(data4D, (4, 4))
    """
    bin_factor = np.array(bin_factor, ndmin=1)
    bf = np.copy(bin_factor)
    bin_factor = np.ones(4)
    bin_factor[2:4] = bf
    ini_shape = np.asarray(data4D.shape)
    fin_shape = (np.ceil(ini_shape / bin_factor)).astype(int)
    big_shape = (fin_shape * bin_factor).astype(int)
    binned_4D = np.zeros(fin_shape[0:4], dtype=data4D.dtype)
    big4D = np.zeros(big_shape[0:4], dtype=data4D.dtype)
    big4D[:, :, 0 : ini_shape[2], 0 : ini_shape[3]] = data4D
    for ii in range(fin_shape[2]):
        for jj in range(fin_shape[3]):
            starter_ii = int(bin_factor[2] * ii)
            stopper_ii = int(bin_factor[2] * (ii + 1))
            starter_jj = int(bin_factor[3] * jj)
            stopper_jj = int(bin_factor[3] * (jj + 1))
            summed_cbed = np.sum(
                big4D[:, :, starter_ii:stopper_ii, starter_jj:stopper_jj], axis=(-1, -2)
            )
            binned_4D[:, :, ii, jj] = summed_cbed
    binned_4D = binned_4D / (bin_factor[2] * bin_factor[3])
    return (binned_4D).astype(data4D.dtype)


def cbed_filter(
    image, circ_vals, med_val=50, sec_med=True, hybridizer=0.25, bit_depth=32
):
    """
    Generate the filtered cross-correlated image for locating disk
    positions
     
    Parameters
    ----------
    image:      ndarray
                The image to be filtered
    circ_vals:  tuple
                Three valued tuple that holds the cross
                correlating circle values where the first
                position is the X position of the cnter, 
                second value is the Y coordinate of the 
                center and the third value is the circle radius.
    med_val:    float, optional
                Deviation from median value to accept in the 
                Sobel filtered image. Default is 50
    sec_med:    bool, Optional
                Tamps out deviation from median values in the
                Sobel filtered image too if True
    hybridizer: float, optional
                The value to use for hybrid cross-correlation.
                Default is 0.25. 0 gives pure cross correlation,
                while 1 gives pure phase correlation
    bit_depth:  int, optional
                Maximum power of 2 to be used for scaling the image
                when taking logarithms. Default is 32
    
    Returns
    -------
    slm_image: ndarray
               The filtered image.
               
    lsc_image: ndarray
               The filtered image cross-correlated with the circle edge
     
    Notes
    -----
    We first generate the circle centered at the X and Y co-ordinates, with 
    the radius given inside the circ_vals tuple. This generated circle is 
    the Sobel filtered to generate an edge of the circle.
    
    Often due to detector issues, or stray muons a single pixel may be 
    much brighter. Also dead pixels can cause individual pixels to be 
    much darker. To remove such errors, and values in the image, we take 
    the median value of the image and then throw any values that are med_val 
    times larger or med_val times smaller than the median. Then we normalize 
    the image from 1 to the 2^bit_depth and then take the log of that image. 
    This generates an image whose scale is between 0 and the bit_depth. To 
    further decrease detector noise, this scaled image is then Gaussian filtered 
    with a single pixel blur, and then finally Sobel filtered. This Sobel
    filtered image is then cross-correlated with the Sobel filtered circle edge.
    
    If there are disks in the image whose size is close to the radius of the 
    circle, then the locations of them now become 2D peaks. If the 
    circle radius is however too small/large rather than 2D peaks at 
    diffraction disk locations, we will observe circles.
    
    Examples
    --------
    This is extremely useful for locating NBED diffraction positions. If you know
    the size and location of the central disk which you can obtain by running 
    `st.util.sobel_circle` on the undiffracted CBED pattern on vacuum as:
    
    >>> beam_x, beam_y, beam_r = st.util.sobel_circle(nodiff_cbed)
    
    Then use the on the Mean_CBED to calculate the disk positions from:
    
    >>> slm_reference, lsc_reference = st.nbed.cbed_filter(Mean_CBED, (beam_x, beam_y, beam_r))
    
    """
    # Generating the circle edge
    center_disk = st.util.make_circle(
        np.asarray(image.shape), circ_vals[0], circ_vals[1], circ_vals[2]
    )
    sobel_center_disk, _ = st.util.sobel(center_disk)

    # Throwing away stray pixel values
    med_image = np.copy(image)
    med_image[med_image > med_val * np.median(med_image)] = med_val * np.median(
        med_image
    )
    med_image[med_image < np.median(med_image) / med_val] = (
        np.median(med_image) / med_val
    )

    # Filtering the image
    slm_image, _ = st.util.sobel(
        scnd.gaussian_filter(st.util.image_logarizer(med_image, bit_depth), 1)
    )
    if sec_med:
        slm_image[slm_image > med_val * np.median(slm_image)] = med_val * np.median(
            slm_image
        )
        slm_image[slm_image < np.median(slm_image) / med_val] = (
            np.median(slm_image) / med_val
        )

    # Cross-correlating it
    lsc_image = st.util.cross_corr(slm_image, sobel_center_disk, hybridizer)
    return slm_image, lsc_image
