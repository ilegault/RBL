import zwoasi as asi
asi.init(r"C:\Program files\ASIStudio\ASICamera2.dll")   # point at the x64 dll
print(asi.list_cameras())                # should show the ASI585MC

cam = asi.Camera(0)
cam.set_control_value(asi.ASI_GAIN, 100)
cam.set_control_value(asi.ASI_EXPOSURE, 20000)   # microseconds = 20 ms
cam.set_image_type(asi.ASI_IMG_RAW16)
img = cam.capture()
print(img.shape, img.dtype, img.min(), img.max())
cam.close()