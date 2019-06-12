import numpy as np
import time
from numba import jit

from pysingfel.util import deprecated

from .convert import *
from .generate import *
from .mapping import *
from .slice import *


######################################################################
# The following functions are utilized to get corrections
######################################################################

def reshape_pixels_position_arrays_to_1d(array):
    """
    Only an abbreviation.

    :param array: The position array.
    :return: Reshaped array.
    """
    array_1d = np.reshape(array, [np.prod(array.shape[:-1]), 3])
    return array_1d


def get_reciprocal_space_pixel_position(pixel_center, wave_vector):
    """
    Obtain the coordinate of each pixel in the reciprocal space.
    :param pixel_center: The coordinate of  the pixel in real space.
    :param wave_vector: The wavevector.
    :return: The array containing the pixel coordinates.
    """
    # reshape the array into a 1d position array
    pixel_center_1d = reshape_pixels_position_arrays_to_1d(pixel_center)

    # Calculate the reciprocal position of each pixel
    wave_vector_norm = np.sqrt(np.sum(np.square(wave_vector)))
    wave_vector_direction = wave_vector / wave_vector_norm

    pixel_center_norm = np.sqrt(np.sum(np.square(pixel_center_1d), axis=1))
    pixel_center_direction = pixel_center_1d / pixel_center_norm[:, np.newaxis]

    pixel_position_reciprocal_1d = wave_vector_norm * (
            pixel_center_direction - wave_vector_direction)

    # restore the pixels shape
    pixel_position_reciprocal = np.reshape(pixel_position_reciprocal_1d, pixel_center.shape)

    return pixel_position_reciprocal


def get_polarization_correction(pixel_center, polarization):
    """
    Obtain the polarization correction.

    :param pixel_center: The position of each pixel in real space.
    :param polarization: The polarization vector of the incident beam.
    :return: The polarization correction array.
    """
    # reshape the array into a 1d position array
    pixel_center_1d = reshape_pixels_position_arrays_to_1d(pixel_center)

    pixel_center_norm = np.sqrt(np.sum(np.square(pixel_center_1d), axis=1))
    pixel_center_direction = pixel_center_1d / pixel_center_norm[:, np.newaxis]

    # Calculate the polarization correction
    polarization_norm = np.sqrt(np.sum(np.square(polarization)))
    polarization_direction = polarization / polarization_norm

    polarization_correction_1d = np.sum(np.square(np.cross(pixel_center_direction,
                                                           polarization_direction)), axis=1)

    # print polarization_correction_1d.shape

    polarization_correction = np.reshape(polarization_correction_1d, pixel_center.shape[0:-1])

    return polarization_correction


def solid_angle(pixel_center, pixel_area, orientation):
    """
    Calculate the solid angle for each pixel.

    :param pixel_center: The position of each pixel in real space. In pixel stack format.
    :param orientation: The orientation of the detector.
    :param pixel_area: The pixel area for each pixel. In pixel stack format.
    :return: Solid angle of each pixel.
    """

    # Use 1D format
    pixel_center_1d = reshape_pixels_position_arrays_to_1d(pixel_center)
    pixel_center_norm_1d = np.sqrt(np.sum(np.square(pixel_center_1d), axis=-1))
    pixel_area_1d = np.reshape(pixel_area, np.prod(pixel_area.shape))

    # Calculate the direction of each pixel.
    pixel_center_direction_1d = pixel_center_1d / pixel_center_norm_1d[:, np.newaxis]

    # Normalize the orientation vector
    orientation_norm = np.sqrt(np.sum(np.square(orientation)))
    orientation_normalized = orientation / orientation_norm

    # The correction induced by projection which is a factor of cosine.
    cosine_1d = np.abs(np.dot(pixel_center_direction_1d, orientation_normalized))

    # Calculate the solid angle ignoring the projection
    solid_angle_1d = np.divide(pixel_area_1d, np.square(pixel_center_norm_1d))
    solid_angle_1d = np.multiply(cosine_1d, solid_angle_1d)

    # Restore the pixel stack format
    solid_angle_stack = np.reshape(solid_angle_1d, pixel_area.shape)

    return solid_angle_stack


def get_reciprocal_position_and_correction(pixel_position, pixel_area,
                                           wave_vector, polarization, orientation):
    """
    Calculate the pixel positions in reciprocal space and all the related corrections.

    :param pixel_position: The position of the pixel in real space.
    :param wave_vector: The wavevector.
    :param polarization: The polarization vector.
    :param orientation: The normal direction of the detector.
    :param pixel_area: The pixel area for each pixel. In pixel stack format.
    :return: pixel_position_reciprocal, pixel_position_reciprocal_norm, polarization_correction,
            geometry_correction
    """
    # Calculate the position and distance in reciprocal space
    pixel_position_reciprocal = get_reciprocal_space_pixel_position(pixel_center=pixel_position,
                                                                    wave_vector=wave_vector)
    pixel_position_reciprocal_norm = np.sqrt(np.sum(np.square(pixel_position_reciprocal), axis=-1))

    # Calculate the corrections.
    polarization_correction = get_polarization_correction(pixel_center=pixel_position,
                                                          polarization=polarization)

    # Because the pixel area in this function is measured in m^2,
    # therefore,the distance has to be in m
    solid_angle_array = solid_angle(pixel_center=pixel_position * 1e-6,
                                    pixel_area=pixel_area,
                                    orientation=orientation)

    return (pixel_position_reciprocal, pixel_position_reciprocal_norm,
            polarization_correction, solid_angle_array)


######################################################################
# The following functions are utilized to get reciprocal space grid mesh
######################################################################

def get_reciprocal_mesh(voxel_num_1d, voxel_length):
    """
    Get a symmetric reciprocal coordinate mesh.

    :param voxel_num_1d: An positive odd integer.
    :param voxel_length: The length of the voxel.
    :return: The mesh.
    """
    voxel_half_num_1d = (voxel_num_1d - 1) / 2

    x_meshgrid = (np.array(range(voxel_num_1d)) - voxel_half_num_1d) * voxel_length
    reciprocal_mesh_stack = np.meshgrid(x_meshgrid, x_meshgrid, x_meshgrid)

    reciprocal_mesh = np.zeros((voxel_num_1d, voxel_num_1d, voxel_num_1d, 3))
    for l in range(3):
        reciprocal_mesh[:, :, :, l] = reciprocal_mesh_stack[l][:, :, :]

    return reciprocal_mesh


######################################################################
# The following functions are utilized to assemble the images
######################################################################

def assemble_image_stack(image_stack, index_map):
    """
    Assemble the image stack to obtain a 2D pattern according to the index map

    :param image_stack: [panel num, panel pixel num x, panel pixel num y]
    :param index_map: [panel num, panel pixel num x, panel pixel num y]
    :return: 2D pattern
    """
    # get boundary
    index_max_x = np.max(index_map[:, :, :, 0])
    index_max_y = np.max(index_map[:, :, :, 1])
    # set holder
    image = np.zeros((index_max_x, index_max_y))
    # loop through the panels
    for l in range(index_map.shape[0]):
        image[index_map[l, :, :, :]] = image_stack[l, :, :]

    return image


def assemble_image_stack_batch(image_stack, index_map):
    """
    Assemble the image stack to obtain a 2D pattern according to the index map

    :param image_stack: [stack num, panel num, panel pixel num x, panel pixel num y]
    :param index_map: [panel num, panel pixel num x, panel pixel num y]
    :return: [stack num, 2d pattern x, 2d pattern y]
    """
    # get boundary
    index_max_x = np.max(index_map[:, :, :, 0])
    index_max_y = np.max(index_map[:, :, :, 1])
    # get stack number and panel number
    stack_num = image_stack.shape[0]
    panel_num = image_stack.shape[1]

    # set holder
    image = np.zeros((stack_num, index_max_x, index_max_y))

    # loop through the panels
    for l in range(panel_num):
        image[:, index_map[l, :, :, 0], index_map[l, :, :, 1]] = image_stack[:, l, :, :]

    return image


######################################################################
# The following functions are utilized to work on the particles
######################################################################

def get_random_translations(atom_pos, beam_focus_size):
    """
    Get translations in real space

    :param num_particles: Number of particles
    :param beam_focus: Radius within which we want the translations
    :return: List of dictionaries containing translations of all particles within the beam focus
    """
    N = len(atom_pos)

    flag = True
    while(flag):
        x_trans = beam_focus_size*np.random.uniform(-1, 1)
        y_trans = beam_focus_size*np.random.uniform(-1, 1)
        trans = [[x_trans, y_trans, 0]]*N
        trans = np.asarray(trans)
        new_pos = atom_pos + trans
        for i in range(N):
            if (np.sqrt(new_pos[i][0]**2 + new_pos[i][1]**2) >= beam_focus_size):
                flag = False
                break
            else:
                flag = False
    return new_pos
