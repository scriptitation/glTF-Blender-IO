# Copyright 2018-2021 The glTF-Blender-IO authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import bpy
from ...io.com.gltf2_io import TextureInfo
from .gltf2_blender_texture import texture
from io_scene_gltf2.io.com.gltf2_io_constants import GLTF_IOR
from .gltf2_blender_image import BlenderImage
from ..exp.gltf2_blender_image import TmpImageGuard, make_temp_image_copy


def specular(mh, location_specular, 
                 location_specular_tint, 
                 specular_socket, 
                 specular_tint_socket, 
                 base_color_image_name,
                 transmission_image_name):
    x_specular, y_specular = location_specular
    x_tint, y_tint = location_specular_tint

    if specular_socket is None:
        return
    if specular_tint_socket is None:
        return

    try:
        ext = mh.pymat.extensions['KHR_materials_specular']
    except Exception:
        return

    import numpy as np

    # First check if we need a texture or not -> retrieve all info needed
    specular_factor = ext.get('specularFactor', 1.0)
    tex_specular_info = ext.get('specularTexture')
    if tex_specular_info is not None:
        tex_specular_info = TextureInfo.from_dict(tex_specular_info)

    specular_color_factor = np.array(ext.get('specularColorFactor', [1.0, 1.0, 1.0])[:3])
    tex_specular_color_info = ext.get('specularColorTexture')
    if tex_specular_color_info is not None:
        tex_specular_color_info = TextureInfo.from_dict(tex_specular_color_info)

    base_color_not_linked = base_color_image_name is None
    base_color = np.array(mh.pymat.pbr_metallic_roughness.base_color_factor or [1, 1, 1])
    tex_base_color = mh.pymat.pbr_metallic_roughness.base_color_texture
    base_color = base_color[:3]

    transmission_not_linked = transmission_image_name is None
    
    try:
        ext_transmission = mh.pymat.extensions['KHR_materials_transmission']
        transmission_factor = ext_transmission.get('transmissionFactor', 0)
        tex_transmission_info = ext_transmission.get('transmissionTexture')
        if tex_transmission_info is not None:
            tex_transmission_info = TextureInfo.from_dict(tex_transmission_info)
    except Exception:
        transmission_factor = 0

    try:
        ext_ior = mh.pymat.extensions['KHR_materials_ior']
        ior = ext_ior.get('ior', GLTF_IOR)
    except:
        ior = GLTF_IOR

    use_texture = tex_specular_info is not None or tex_specular_color_info is not None \
        or transmission_not_linked is False or base_color_not_linked is False

    
    if not use_texture:

        def luminance(c):
            return 0.3 * c[0] + 0.6 * c[1] + 0.1 * c[2]

        def normalize(c):
            assert(len(c) == 3)
            l = luminance(c)
            if l == 0:
                return c
            return np.array([c[0] / l, c[1] / l, c[2] / l])

        f0_from_ior = ((ior - 1)/(ior + 1))**2
        lum_specular_color = luminance(specular_color_factor)
        blender_specular = ((lum_specular_color - transmission_factor) / (1 - transmission_factor)) * (1 / 0.08) * f0_from_ior
        blender_specular_tint = luminance((normalize(specular_color_factor) - 1) / (normalize(base_color) - 1))
        if blender_specular_tint < 0 or blender_specular_tint > 1:
            # TODOExt Warning clamping
            blender_specular_tint = np.maximum(np.minimum(blender_specular_tint, 1), 0)

        specular_socket.default_value = blender_specular
        specular_tint_socket.default_value = blender_specular_tint
        # Note: blender_specular can be greater 1. The Blender documentation permits this.

        return
    else:
        # Need to create a texture
        # First, retrieve and create all images needed

        # Base Color is already created
        # Transmission is already created
        # specularTexture --> Not needed, as not used during conversion, but we need to get image size if needed
        specular_image_name = None
        if tex_specular_info is not None:
            tex_specular = mh.gltf.data.textures[tex_specular_info.index]
            if tex_specular.source is not None:
                BlenderImage.create(mh.gltf, tex_specular.source)

                # Retrieve image just created
                pyimg = mh.gltf.data.images[tex_specular.source]
                specular_image_name = pyimg.blender_image_name


        # specularColorTexture : Need to be created
        specularcolor_image_name = None
        if tex_specular_color_info is not None:
            tex_specular_color = mh.gltf.data.textures[tex_specular_color_info.index]
            if tex_specular_color.source is not None:
                BlenderImage.create(mh.gltf, tex_specular_color.source)

                # Retrieve image just created
                pyimg = mh.gltf.data.images[tex_specular_color.source]
                specularcolor_image_name = pyimg.blender_image_name

        stack3 = lambda v: np.dstack([v]*3)

        texts = {
            base_color_image_name : 'basecolor',
            transmission_image_name : 'transmission',
            specularcolor_image_name : 'spec',
        }
        
        images = [(name, bpy.data.images[name]) for name in [base_color_image_name, transmission_image_name, specularcolor_image_name] if name is not None]
        if len(images) == 0:
            # So this is the specular texture...
            width = bpy.data.images[specular_image_name].size[0]
            height = bpy.data.images[specular_image_name].size[1]
        else:
            width = max(image[1].size[0] for image in images)
            height = max(image[1].size[1] for image in images)

        buffers = {}
        for name, image in images:
            tmp_buf = np.empty(width * height * 4, np.float32)
            
            if image.size[0] == width and image.size[1] == height:
                image.pixels.foreach_get(tmp_buf)
            else:
                # Image is the wrong size; make a temp copy and scale it.
                with TmpImageGuard() as guard:
                    make_temp_image_copy(guard, src_image=image)
                    tmp_image = guard.image
                    tmp_image.scale(width, height)
                    tmp_image.pixels.foreach_get(tmp_buf)

            buffers[texts[name]] = np.reshape(tmp_buf, [width, height, 4])
            buffers[texts[name]] = buffers[texts[name]][:,:,:3]

            # Manage factors
            if name == transmission_image_name:
                buffers[texts[name]] = stack3(buffers[texts[name]][:,:,0])  # Transmission : keep only R channel

                buffers[texts[name]] *= stack3(transmission_factor)

            elif name == base_color_image_name:
                buffers[texts[name]] *= base_color

            elif name == specularcolor_image_name:
                buffers[texts[name]] *= specular_color_factor

        # Create buffer if there is no image
        if 'basecolor' not in buffers.keys():
            buffers['basecolor'] = np.full((width, height, 3), base_color)
        if 'transmission' not in buffers.keys():
            buffers['transmission'] = np.full((width, height, 3), transmission_factor)
        if 'spec' not in buffers.keys():
            buffers['spec'] = np.full((width, height, 3), specular_color_factor)

        # Calculation

        luminance = lambda c: 0.3 * c[:,:,0] + 0.6 * c[:,:,1] + 0.1 * c[:,:,2]
        def normalize(c):
            l = luminance(c)
            # TODOExt Manage all 0
            return c / stack3(l)

        f0_from_ior = ((ior - 1)/(ior + 1))**2
        lum_specular_color = stack3(luminance(buffers['spec']))
        blender_specular = ((lum_specular_color - buffers['transmission']) / (1 - buffers['transmission'])) * (1 / 0.08) * f0_from_ior
        blender_specular_tint = luminance((normalize(buffers['spec']) - 1) / (normalize(buffers['basecolor']) - 1))
        blender_specular_tint = np.clip(blender_specular_tint, 0.0, 1.0)
        blender_specular_tint = stack3(blender_specular_tint)

        blender_specular = np.dstack((blender_specular, np.ones((height, width)))) # Set alpha to 1
        blender_specular_tint = np.dstack((blender_specular_tint, np.ones((height, width)))) # Set alpha to 1

        blender_specular = np.reshape(blender_specular, width * height * 4)
        blender_specular_tint = np.reshape(blender_specular_tint, width * height * 4)

        # Create images in Blender, width and height are dummy values, then set packed file data
        # print(blender_specular.dtype)
        blender_image_spec = bpy.data.images.new('Specular', width, height)
        blender_image_spec.pixels.foreach_set(np.float32(blender_specular))
        blender_image_spec.pack()

        blender_image_tint = bpy.data.images.new('Specular Tint', width, height)
        blender_image_tint.pixels.foreach_set(np.float32(blender_specular_tint))
        blender_image_tint.pack()
   
        # Create Textures in Blender
        tex_info = tex_specular_info
        if tex_info is None:
            tex_info = tex_specular_color_info
        if tex_info is None:
            tex_info = tex_transmission_info
        if tex_info is None:
            tex_info = tex_base_color

        texture(
            mh,
            tex_info=tex_info,
            label='SPECULAR',
            location=(x_specular, y_specular),
            is_data=True,
            color_socket=specular_socket,
            forced_image=blender_image_spec
        )

        tex_info = tex_specular_color_info
        if tex_info is None:
            tex_info = tex_specular_info
        if tex_info is None:
            tex_info = tex_transmission_info
        if tex_info is None:
            tex_info = tex_base_color

        texture(
            mh,
            tex_info=tex_info,
            label='SPECULAR TINT',
            location=(x_tint, y_tint),
            is_data=True,
            color_socket=specular_tint_socket,
            forced_image=blender_image_tint
        )
