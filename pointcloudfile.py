#!/usr/bin/env python3
"""A specialised io module for .ply files containing XYZRGB points.

This is useful for all the example forest data I've seen so far, and is
easy enough to extend later for other attributes.

Returns generator objects, which yield points, due to out-of-memory issues.
"""

import struct
import os.path
from tempfile import SpooledTemporaryFile
from collections import namedtuple

UTM_offset = namedtuple('UTM_offset', ['x', 'y', 'z', 'zone'])

def UTM_offset_for(filename, zone=55):
    """Return the (x, y, z) offset for a Pix4D .ply cloud."""
    offset = filename[:-4] + '_ply_offset.xyz'
    if not os.path.isfile(offset):
        return UTM_offset(0, 0, 0, zone)
    with open(offset) as f:
        x, y, z = (float(n) for n in f.readline().strip().split(' '))
        return UTM_offset(x, y, z, zone)

def check_input(fname, ending=''):
    """Checks that the file exists and has the right ending"""
    if not os.path.isfile(fname):
        raise FileNotFoundError(
            'Cannot read points from a nonexistent file')
    if not fname.endswith(ending):
        raise ValueError('Tried to read file type {}, expected {}.'.format(
            fname[-4:], ending))

def read(fname):
    """Passes the file to a read function for that format."""
    if fname.endswith('_point_cloud_part_1.ply'):
        parts, p = [fname], 1
        stub = fname.replace('_point_cloud_part_1.ply', '')
        while True:
            p += 1
            part = stub + '_point_cloud_part_{}.ply'.format(p)
            if os.path.isfile(part):
                parts.append(part)
            else:
                return _read_pix4d_ply_parts(parts)
    return _read_ply(fname)

def _read_pix4d_ply_parts(fname_list):
    """Read from a collection of ply files as if they were one file.
    Pix4D usually exports point clouds in parts, with a UTM offset for the
    origin.  We can thus read from all (though only write to one file."""
    for f in fname_list:
        check_input(f, '.ply')
    first = fname_list.pop(0)
    *offset, zone = UTM_offset_for(first)
    for point in _read_ply(first):
        yield point
    for f in fname_list:
        dx, dy, dz = [b - a for a, b in zip(offset, UTM_offset_for(f))]
        for x, y, z, r, g, b in _read_ply(f):
            yield x+dx, y+dy, z+dz, r, g, b

def _read_ply(fname):
    """Opens the specified file, and returns a point set in the format required
    by attributes_from_cloud.  Only handles xyzrgb point clouds, but that's
    a fine subset of the format.  See http://paulbourke.net/dataformats/ply/"""
    #pylint:disable=too-many-branches,too-many-locals,too-many-statements
    check_input(fname, '.ply')
    with open(fname, 'rb') as f:
        raw_header = []
        while True:
            raw_header.append(next(f))
            if raw_header[-1].startswith(b'end_header'):
                # ply files can have basically any line ending...
                lineending = raw_header[0].replace(b'ply', b'')
                break
        header = [line.strip().split(b' ') for line in raw_header]
        header = [(line[2], line[1]) for line in header
                  if line[0] == b'property']
        cols = {'r': b'red', 'g': b'green', 'b': b'blue'}
        if any(b' diffuse_' in l for l in raw_header):
            cols = {'r': b'diffuse_red', 'g': b'diffuse_green',
                    'b': b'diffuse_blue'}
        if b'format ascii 1.0'+lineending in raw_header:
            head = {}
            for i, line in enumerate(header):
                head[line[0]] = (i, b'float' in line[1] or line[1] == b'double')
                types = [float if s[1] else int for s in
                         (b'x', b'y', b'z', cols['r'], cols['g'], cols['b'])]
            for line in f:
                point = line.strip().split(b' ')
                yield tuple(t(n) for t, n in zip(types, point))
        else:
            head = [h[0] for h in header]
            idx = {'x':head.index(b'x'), 'y':head.index(b'y'),
                   'z':head.index(b'z'), 'r':head.index(cols['r']),
                   'g':head.index(cols['g']), 'b':head.index(cols['b'])}
            form_str = '<'
            if b'format binary_big_endian 1.0'+lineending in raw_header:
                form_str = '>'
            for _, type_ in header:
                if type_ == b'float':
                    form_str += 'f'
                elif type_ == b'uchar':
                    form_str += 'B'
                elif type_ == b'char':
                    form_str += 'b'
                elif type_ == b'short':
                    form_str += 'h'
                elif type_ == b'ushort':
                    form_str += 'H'
                elif type_ == b'int':
                    form_str += 'i'
                elif type_ == b'uint':
                    form_str += 'I'
                elif type_ == b'double':
                    form_str += 'd'
            point = struct.Struct(form_str)
            raw = f.read(point.size)
            ind = (idx['x'], idx['y'], idx['z'], idx['r'], idx['g'], idx['b'])
            if ind == tuple(range(6)):
                while raw:
                    p_tup = point.unpack(raw)
                    yield p_tup
                    raw = f.read(point.size)
            else:
                while raw:
                    p_tup = point.unpack(raw)
                    yield tuple(p_tup[i] for i in ind)
                    raw = f.read(point.size)

def write(cloud, fname, utm_coords=None):
    """Write the given cloud to disk."""
    binary = struct.Struct('<fffBBB')
    with SpooledTemporaryFile(max_size=2**20, mode='w+b') as temp_storage:
        count = 0
        for count, point in enumerate(cloud, start=1):
            temp_storage.write(binary.pack(*point))
        temp_storage.seek(0)
        header = ['ply', 'format binary_little_endian 1.0',
                  'element vertex {}'.format(count),
                  'property float x',
                  'property float y',
                  'property float z',
                  'property uchar red',
                  'property uchar green',
                  'property uchar blue',
                  'end_header', '']
        if utm_coords is not None and len(utm_coords) == 4:
            header.insert(-2, 'comment UTM easting northing altitude zone ' +
                          '{:.2f} {:.2f} {:.2f} {:d}'.format(*utm_coords))
        with open(fname, 'wb') as f:
            f.write('\n'.join(header).encode('ascii'))
            chunk = temp_storage.read(8192)
            while chunk:
                f.write(chunk)
                chunk = temp_storage.read(8192)

