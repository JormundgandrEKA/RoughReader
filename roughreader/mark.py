"""Geometry of the app mark, on a 64-unit grid. Shared by Qt and the icon builder."""

FELDGRAU = "#4D5D53"
LIGHT_GREY = "#D6D9D7"
BURGUNDY = "#800020"

CUT = 13
PLATE = [(CUT, 0), (64, 0), (64, 64 - CUT), (64 - CUT, 64), (0, 64), (0, CUT)]
STEM = [(13, 13), (23, 13), (23, 51), (13, 51)]
BOWL = [(27, 13), (42, 13), (56, 24.5), (42, 36), (27, 36), (27, 28), (39, 28), (43.5, 24.5), (39, 21), (27, 21)]
LEG = [(32, 36), (43, 36), (56, 51), (45, 51)]

SHAPES = [(PLATE, FELDGRAU), (STEM, BURGUNDY), (BOWL, LIGHT_GREY), (LEG, LIGHT_GREY)]
