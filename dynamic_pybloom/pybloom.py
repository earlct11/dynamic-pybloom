# -*- encoding: utf-8 -*-
"""This module implements a bloom filter probabilistic data structure,
a Scalable Bloom Filter that grows in size as your add more items to it
without increasing the false positive error_rate, and a Dynamic Bloom Filter
which allows you to grow in size while still being able to intersect and union
to other Dynamic Bloom Filter.

Requires the bitarray library: http://pypi.python.org/pypi/bitarray/

    >>> from dynamic_pybloom import BloomFilter
    >>> f = BloomFilter(capacity=10000, error_rate=0.001)
    >>> for i in range_fn(0, f.capacity):
    ...     _ = f.add(i)
    ...
    >>> 0 in f
    True
    >>> f.capacity in f
    False
    >>> len(f) <= f.capacity
    True
    >>> (1.0 - (len(f) / float(f.capacity))) <= f.error_rate + 2e-18
    True

    >>> from dynamic_pybloom import ScalableBloomFilter
    >>> sbf = ScalableBloomFilter(mode=ScalableBloomFilter.SMALL_SET_GROWTH)
    >>> count = 10000
    >>> for i in range_fn(0, count):
    ...     _ = sbf.add(i)
    ...
    >>> sbf.capacity > count
    True
    >>> len(sbf) <= count
    True
    >>> (1.0 - (len(sbf) / float(count))) <= sbf.error_rate + 2e-18
    True
    >>> from dynamic_pybloom import ScalableBloomFilter
    >>> sbf = DynamicBloomFilter()
    >>> count = 10000
    >>> for i in range_fn(0, count):
    ...     _ = sbf.add(i)
    ...
    >>> sbf.capacity > count
    True
    >>> len(sbf) <= count
    True

"""
from __future__ import absolute_import
import math
import hashlib
from dynamic_pybloom.utils import range_fn, is_string_io, running_python_3
from struct import unpack, pack, calcsize
from binascii import hexlify, unhexlify

try:
    import bitarray
except ImportError:
    raise ImportError('dynamic_pybloom requires bitarray >= 0.3.4')

__version__ = '3.1'
__author__ = "Jay Baird <jay.baird@me.com>, Bob Ippolito <bob@redivi.com>,\
               Marius Eriksen <marius@monkey.org>,\
               Alex Brasetvik <alex@brasetvik.com>,\
               Matt Bachmann <bachmann.matt@gmail.com>,\
               Sam Findler <samuel.findler@market-predictions.com>"


def make_hashfuncs(num_slices, num_bits):
    if num_bits >= (1 << 31):
        fmt_code, chunk_size = 'Q', 8
    elif num_bits >= (1 << 15):
        fmt_code, chunk_size = 'I', 4
    else:
        fmt_code, chunk_size = 'H', 2
    total_hash_bits = 8 * num_slices * chunk_size
    if total_hash_bits > 384:
        hashfn = hashlib.sha512
    elif total_hash_bits > 256:
        hashfn = hashlib.sha384
    elif total_hash_bits > 160:
        hashfn = hashlib.sha256
    elif total_hash_bits > 128:
        hashfn = hashlib.sha1
    else:
        hashfn = hashlib.md5
    fmt = fmt_code * (hashfn().digest_size // chunk_size)
    num_salts, extra = divmod(num_slices, len(fmt))
    if extra:
        num_salts += 1
    salts = tuple(hashfn(hashfn(pack('I', i)).digest()) for i in range_fn(num_salts))
    def _make_hashfuncs(key):
        if running_python_3:
            if isinstance(key, str):
                key = key.encode('utf-8')
            else:
                key = str(key).encode('utf-8')
        else:
            if isinstance(key, unicode):
                key = key.encode('utf-8')
            else:
                key = str(key)
        i = 0
        for salt in salts:
            h = salt.copy()
            h.update(key)
            for uint in unpack(fmt, h.digest()):
                yield uint % num_bits
                i += 1
                if i >= num_slices:
                    return

    return _make_hashfuncs


class BloomFilter(object):
    FILE_FMT = b'<dQQQQ'

    def __init__(self, capacity, error_rate=0.001):
        """Implements a space-efficient probabilistic data structure

        capacity
            this BloomFilter must be able to store at least *capacity* elements
            while maintaining no more than *error_rate* chance of false
            positives
        error_rate
            the error_rate of the filter returning false positives. This
            determines the filters capacity. Inserting more than capacity
            elements greatly increases the chance of false positives.

        >>> b = BloomFilter(capacity=100000, error_rate=0.001)
        >>> b.add("test")
        False
        >>> "test" in b
        True

        """
        if not (0 < error_rate < 1):
            raise ValueError("Error_Rate must be between 0 and 1.")
        if not capacity > 0:
            raise ValueError("Capacity must be > 0")
        # given M = num_bits, k = num_slices, P = error_rate, n = capacity
        #       k = log2(1/P)
        # solving for m = bits_per_slice
        # n ~= M * ((ln(2) ** 2) / abs(ln(P)))
        # n ~= (k * m) * ((ln(2) ** 2) / abs(ln(P)))
        # m ~= n * abs(ln(P)) / (k * (ln(2) ** 2))
        num_slices = int(math.ceil(math.log(1.0 / error_rate, 2)))
        bits_per_slice = int(math.ceil(
            (capacity * abs(math.log(error_rate))) /
            (num_slices * (math.log(2) ** 2))))
        self._setup(error_rate, num_slices, bits_per_slice, capacity, 0)
        self.bitarray = bitarray.bitarray(self.num_bits, endian='little')
        self.bitarray.setall(False)

    def _setup(self, error_rate, num_slices, bits_per_slice, capacity, count):
        self.error_rate = error_rate
        self.num_slices = num_slices
        self.bits_per_slice = bits_per_slice
        self.capacity = capacity
        self.count = count
        self.num_bits = num_slices * bits_per_slice
        self.make_hashes = make_hashfuncs(self.num_slices, self.bits_per_slice)

    def __contains__(self, key):
        """Tests a key's membership in this bloom filter.

        >>> b = BloomFilter(capacity=100)
        >>> b.add("hello")
        False
        >>> "hello" in b
        True

        """
        bits_per_slice = self.bits_per_slice
        bitarray = self.bitarray
        hashes = self.make_hashes(key)
        offset = 0
        for k in hashes:
            if not bitarray[offset + k]:
                return False
            offset += bits_per_slice
        return True

    def __str__(self):
        """
        export as string to be sent over network or between programming languages
        :return: compressed string representation
        """
        return ":".join([str(self.error_rate), str(self.num_slices),
                        str(self.bits_per_slice), str(self.capacity),
                        str(self.count), self.bitarray.endian(),
                         hexlify(self.bitarray.tobytes())])

    @classmethod
    def from_str(cls, s):
        """
        uncompress string and make the bloom filter from it, complement to the __str__ method
        :param s:
        :return:
        """
        filter = cls(1)
        values = s.split(":")
        filter._setup(float(values[0]), int(values[1]), int(values[2]), int(values[3]), int(values[4]))
        filter.bitarray = bitarray.bitarray(endian=values[5])
        filter.bitarray.frombytes(unhexlify(values[6]))
        return filter

    def __len__(self):
        """Return the number of keys stored by this bloom filter."""
        return self.count

    def add(self, key, skip_check=False):
        """ Adds a key to this bloom filter. If the key already exists in this
        filter it will return True. Otherwise False.

        >>> b = BloomFilter(capacity=100)
        >>> b.add("hello")
        False
        >>> b.add("hello")
        True
        >>> b.count
        1

        """
        bitarray = self.bitarray
        bits_per_slice = self.bits_per_slice
        hashes = self.make_hashes(key)
        found_all_bits = True
        if self.count > self.capacity:
            raise IndexError("BloomFilter is at capacity")
        offset = 0
        for k in hashes:
            if not skip_check and found_all_bits and not bitarray[offset + k]:
                found_all_bits = False
            self.bitarray[offset + k] = True
            offset += bits_per_slice

        if skip_check:
            self.count += 1
            return False
        elif not found_all_bits:
            self.count += 1
            return False
        else:
            return True

    def copy(self):
        """Return a copy of this bloom filter.
        """
        new_filter = BloomFilter(self.capacity, self.error_rate)
        new_filter.bitarray = self.bitarray.copy()
        new_filter.count = self.count
        return new_filter

    def union(self, other):
        """ Calculates the union of the two underlying bitarrays and returns
        a new bloom filter object."""
        if self.capacity != other.capacity or \
            self.error_rate != other.error_rate:
            raise ValueError("Unioning filters requires both filters to have \
both the same capacity and error rate")
        new_bloom = self.copy()
        new_bloom.bitarray = new_bloom.bitarray | other.bitarray

        # count calculation from http://www.l3s.de/~papapetrou/publications/Bloomfilters-DAPD.pdf
        try:
            new_bloom.count = int(math.log(1 - float(new_bloom.bitarray.count()) / len(new_bloom.bitarray)) / \
                                  (new_bloom.num_slices * math.log(1 - 1. / len(new_bloom.bitarray))))
        except:
            new_bloom.count = len(new_bloom.bitarray)
        return new_bloom

    def __or__(self, other):
        return self.union(other)

    def intersection(self, other):
        """ Calculates the intersection of the two underlying bitarrays and returns
        a new bloom filter object."""
        if self.capacity != other.capacity or \
            self.error_rate != other.error_rate:
            raise ValueError("Intersecting filters requires both filters to \
have equal capacity and error rate")
        new_bloom = self.copy()
        new_bloom.bitarray = new_bloom.bitarray & other.bitarray

        # count calculation from http://www.l3s.de/~papapetrou/publications/Bloomfilters-DAPD.pdf
        try:
            new_bloom.count = int(math.log(1 - float(new_bloom.bitarray.count()) / len(new_bloom.bitarray)) / \
                                  (new_bloom.num_slices * math.log(1 - 1. / len(new_bloom.bitarray))))
        except:
            new_bloom.count = len(new_bloom.bitarray)
        return new_bloom

    def __and__(self, other):
        return self.intersection(other)

    def tofile(self, f):
        """Write the bloom filter to file object `f'. Underlying bits
        are written as machine values. This is much more space
        efficient than pickling the object."""
        f.write(pack(self.FILE_FMT, self.error_rate, self.num_slices,
                     self.bits_per_slice, self.capacity, self.count))
        (f.write(self.bitarray.tobytes()) if is_string_io(f)
         else self.bitarray.tofile(f))

    @classmethod
    def fromfile(cls, f, n=-1):
        """Read a bloom filter from file-object `f' serialized with
        ``BloomFilter.tofile''. If `n' > 0 read only so many bytes."""
        headerlen = calcsize(cls.FILE_FMT)

        if 0 < n < headerlen:
            raise ValueError('n too small!')

        filter = cls(1)  # Bogus instantiation, we will `_setup'.
        filter._setup(*unpack(cls.FILE_FMT, f.read(headerlen)))
        filter.bitarray = bitarray.bitarray(endian='little')
        if n > 0:
            (filter.bitarray.frombytes(f.read(n-headerlen)) if is_string_io(f)
             else filter.bitarray.fromfile(f, n - headerlen))
        else:
            (filter.bitarray.frombytes(f.read()) if is_string_io(f)
             else filter.bitarray.fromfile(f))
        if filter.num_bits != len(filter.bitarray) and \
               (filter.num_bits + (8 - filter.num_bits % 8)
                != len(filter.bitarray):
            raise ValueError('Bit length mismatch!')

        return filter

    def __getstate__(self):
        d = self.__dict__.copy()
        del d['make_hashes']
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.make_hashes = make_hashfuncs(self.num_slices, self.bits_per_slice)


class ScalableBloomFilter(object):
    SMALL_SET_GROWTH = 2 # slower, but takes up less memory
    LARGE_SET_GROWTH = 4 # faster, but takes up more memory faster
    FILE_FMT = '<idQd'

    def __init__(self, initial_capacity=100, error_rate=0.001,
                 mode=SMALL_SET_GROWTH):
        """Implements a space-efficient probabilistic data structure that
        grows as more items are added while maintaining a steady false
        positive rate

        initial_capacity
            the initial capacity of the filter
        error_rate
            the error_rate of the filter returning false positives. This
            determines the filters capacity. Going over capacity greatly
            increases the chance of false positives.
        mode
            can be either ScalableBloomFilter.SMALL_SET_GROWTH or
            ScalableBloomFilter.LARGE_SET_GROWTH. SMALL_SET_GROWTH is slower
            but uses less memory. LARGE_SET_GROWTH is faster but consumes
            memory faster.

        >>> b = ScalableBloomFilter(initial_capacity=512, error_rate=0.001, \
                                    mode=ScalableBloomFilter.SMALL_SET_GROWTH)
        >>> b.add("test")
        False
        >>> "test" in b
        True
        >>> unicode_string = u'¡'
        >>> b.add(unicode_string)
        False
        >>> unicode_string in b
        True
        """
        if not error_rate or error_rate < 0:
            raise ValueError("Error_Rate must be a decimal less than 0.")
        self._setup(mode, 0.9, initial_capacity, error_rate)
        self.filters = []

    def _setup(self, mode, ratio, initial_capacity, error_rate):
        self.scale = mode
        self.ratio = ratio
        self.initial_capacity = initial_capacity
        self.error_rate = error_rate

    def __contains__(self, key):
        """Tests a key's membership in this bloom filter.

        >>> b = ScalableBloomFilter(initial_capacity=100, error_rate=0.001, \
                                    mode=ScalableBloomFilter.SMALL_SET_GROWTH)
        >>> b.add("hello")
        False
        >>> "hello" in b
        True

        """
        for f in reversed(self.filters):
            if key in f:
                return True
        return False

    def add(self, key):
        """Adds a key to this bloom filter.
        If the key already exists in this filter it will return True.
        Otherwise False.

        >>> b = ScalableBloomFilter(initial_capacity=100, error_rate=0.001, \
                                    mode=ScalableBloomFilter.SMALL_SET_GROWTH)
        >>> b.add("hello")
        False
        >>> b.add("hello")
        True

        """
        if key in self:
            return True
        if not self.filters:
            filter = BloomFilter(
                capacity=self.initial_capacity,
                error_rate=self.error_rate * (1.0 - self.ratio))
            self.filters.append(filter)
        else:
            filter = self.filters[-1]
            if filter.count >= filter.capacity:
                filter = BloomFilter(
                    capacity=filter.capacity * self.scale,
                    error_rate=filter.error_rate * self.ratio)
                self.filters.append(filter)
        filter.add(key, skip_check=True)
        return False

    @property
    def capacity(self):
        """Returns the total capacity for all filters in this SBF"""
        return sum(f.capacity for f in self.filters)

    @property
    def count(self):
        return len(self)

    def __str__(self):
        return ",".join([str(self.scale), str(self.ratio),
                        str(self.initial_capacity), str(self.error_rate),
                        "|".join([str(filter) for filter in self.filters])])

    @classmethod
    def from_str(cls, s):
        filter = cls(1)
        values = s.split(",")
        filter._setup(int(values[0]), float(values[1]), int(values[2]), float(values[3]))
        if values[4]:
            filters = []
            for item in values[4].split("|"):
                filters.append(BloomFilter.from_str(item))
        filter.filters = filters
        return filter

    def tofile(self, f):
        """Serialize this ScalableBloomFilter into the file-object
        `f'."""
        f.write(pack(self.FILE_FMT, self.scale, self.ratio,
                     self.initial_capacity, self.error_rate))

        # Write #-of-filters
        f.write(pack(b'<l', len(self.filters)))

        if len(self.filters) > 0:
            # Then each filter directly, with a header describing
            # their lengths.
            headerpos = f.tell()
            headerfmt = b'<' + b'Q'*(len(self.filters))
            f.write(b'.' * calcsize(headerfmt))
            filter_sizes = []
            for filter in self.filters:
                begin = f.tell()
                filter.tofile(f)
                filter_sizes.append(f.tell() - begin)

            f.seek(headerpos)
            f.write(pack(headerfmt, *filter_sizes))

    @classmethod
    def fromfile(cls, f):
        """Deserialize the ScalableBloomFilter in file object `f'."""
        filter = cls()
        filter._setup(*unpack(cls.FILE_FMT, f.read(calcsize(cls.FILE_FMT))))
        nfilters, = unpack(b'<l', f.read(calcsize(b'<l')))
        if nfilters > 0:
            header_fmt = b'<' + b'Q'*nfilters
            bytes = f.read(calcsize(header_fmt))
            filter_lengths = unpack(header_fmt, bytes)
            for fl in filter_lengths:
                filter.filters.append(BloomFilter.fromfile(f, fl))
        else:
            filter.filters = []

        return filter

    def __len__(self):
        """Returns the total number of elements stored in this SBF"""
        return sum(f.count for f in self.filters)


class DynamicBloomFilter(object):
    FILE_FMT = '<iQd'

    def __init__(self, base_capacity=100, max_capacity=1000000, error_rate=0.001):

        """ Similar to ScalableBloomFilter, except it is built out of uniform size and
        error_rate bloom filters so that the operations of union and intersection
        are possible.

        base_capacity
            the capacity of a single one of the bloom filters that makes up the whole filter
        max_capacity
            the most possible elements that can go into the filter. used in conjunction with
            the error_rate and base capacity to determine the individual error_rates of the
             base bloom filters
        error_rate
            the maximum error_rate of the filter returning false positives. as long as the
            number of items is under the max_capacity, the error_rate will remain beneath
            this level.

        >>> b = DynamicBloomFilter(base_capacity=512, max_capacity=512*5, error_rate=0.001)
        >>> b.add("test")
        False
        >>> "test" in b
        True
        >>> unicode_string = u'¡'
        >>> b.add(unicode_string)
        False
        >>> unicode_string in b
        True
        """
        if not error_rate or error_rate < 0:
            raise ValueError("error_rate must be a decimal greater than 0.")
        self._setup(base_capacity, max_capacity, error_rate)
        self.filters = []

    def _setup(self, base_capacity, max_capacity, error_rate):
        self.individual_error_rate = 1 - math.exp(math.log(1 - error_rate)/math.ceil(max_capacity/base_capacity))
        self.max_error_rate = error_rate
        self.max_capacity = max_capacity
        self.base_capacity = base_capacity

    def __contains__(self, key):
        """Tests a key's membership in this bloom filter.

        >>> b = DynamicBloomFilter(base_capacity=512, max_capacity=512*5, error_rate=0.001)
        >>> b.add("hello")
        False
        >>> "hello" in b
        True

        """
        for f in reversed(self.filters):
            if key in f:
                return True
        return False

    def add(self, key):
        """Adds a key to this bloom filter.
        If the key already exists in this filter it will return True.
        Otherwise False.

        >>> b = DynamicBloomFilter(base_capacity=512, max_capacity=512*5, error_rate=0.001)
        >>> b.add("hello")
        False
        >>> b.add("hello")
        True

        """
        if key in self:
            return True
        if not self.filters:
            filter = BloomFilter(
                capacity=self.base_capacity,
                error_rate=self.individual_error_rate)
            self.filters.append(filter)
        else:
            filter = self.filters[-1]
            if filter.count >= filter.capacity:
                filter = BloomFilter(
                    capacity=self.base_capacity,
                    error_rate=self.individual_error_rate)
                self.filters.append(filter)
        filter.add(key, skip_check=True)
        return False

    def union(self, other):
        """ Is used to perform a union operation, will keep the error_rate of each bloom_filter
        but you will end up with len(others.filters) + len(self.filters) number of bloom filters
        This accounts that virtually all of the filters in each of the DBFs will be filled to capacity, though
        it can lead to some redundancy in the individual bloom filters. keep in mind that if the count > capacity
        after the union you have a greater possible error_rate"""
        if self.base_capacity != other.base_capacity or \
           self.individual_error_rate != other.individual_error_rate:
            raise ValueError("Intersecting dynamic filters requires both filters to \
                                  have equal base capacity and maximum_capacity")

        new_bloom = DynamicBloomFilter(base_capacity=self.base_capacity,
                                       max_capacity=self.max_capacity,
                                       error_rate=self.max_error_rate)
        my_filters = self.filters
        other_filters = []
        for filter in other.filters:
            other_filters.append(filter.copy())
        for i in range(0, len(my_filters)):
            found_union_mate = False
            for j in range(0, len(other_filters)):
                other_filter_index = len(other_filters) - 1 - j
                union_filter = (my_filters[i] | other_filters[other_filter_index])
                if(union_filter.count < self.base_capacity):
                    other_filters[other_filter_index] = union_filter
                    found_union_mate = True
                    break
            if(not found_union_mate):
                other_filters.append(my_filters[i])
        new_bloom.filters = other_filters
        return new_bloom

    def __or__(self, other):
        return self.union(other)

    def intersection(self, other):
        """ Calculates the intersection of the bitarrays of the base bloomfilters and returns
        a new bloom filter object.  In principal using this should keep the error_rate and the number of
        filters constant"""
        if self.base_capacity != other.base_capacity or \
           self.individual_error_rate != other.individual_error_rate:
            raise ValueError("Intersecting dynamic filters requires both filters to \
                              have equal base capacity and maximum_capacity")

        new_bloom = DynamicBloomFilter(base_capacity=self.base_capacity,
                                       max_capacity=self.max_capacity,
                                       error_rate=self.max_error_rate)
        for bloom_filter in self.filters:
            bloom = BloomFilter(capacity=self.base_capacity, error_rate=self.individual_error_rate)
            for other_filter in other.filters:
                bloom = bloom or (bloom_filter and other_filter)
            new_bloom.filters.append(bloom)
        return new_bloom

    def __and__(self, other):
        return self.intersection(other)

    @property
    def capacity(self):
        """Returns the total capacity for all filters in this DBF"""
        return sum(f.capacity for f in self.filters)

    @property
    def count(self):
        return len(self)

    def __len__(self):
        """Returns the total number of elements stored in this DBF"""
        return sum(f.count for f in self.filters)

    def __str__(self):
        return ",".join([str(self.base_capacity), str(self.max_capacity),
                        str(self.max_error_rate),
                        "|".join([str(filter) for filter in self.filters])])

    @classmethod
    def from_str(cls, s):
        filter = cls(1)
        values = s.split(",")
        filter._setup(int(values[0]), int(values[1]), float(values[2]))
        if values[3]:
            filters = []
            for item in values[3].split("|"):
                filters.append(BloomFilter.from_str(item))
        filter.filters = filters
        return filter

    def tofile(self, f):
        """Serialize this DynamicBloomFilter into the file-object
        `f'."""
        f.write(pack(self.FILE_FMT, self.base_capacity, self.max_capacity,
                     self.max_error_rate))

        # Write #-of-filters
        f.write(pack(b'<l', len(self.filters)))

        if len(self.filters) > 0:
            # Then each filter directly, with a header describing
            # their lengths.
            headerpos = f.tell()
            headerfmt = b'<' + b'Q' * (len(self.filters))
            f.write(b'.' * calcsize(headerfmt))
            filter_sizes = []
            for filter in self.filters:
                begin = f.tell()
                filter.tofile(f)
                filter_sizes.append(f.tell() - begin)

            f.seek(headerpos)
            f.write(pack(headerfmt, *filter_sizes))

    @classmethod
    def fromfile(cls, f):
        """Deserialize the DynamicBloomFilter in file object `f'."""
        filter = cls()
        filter._setup(*unpack(cls.FILE_FMT, f.read(calcsize(cls.FILE_FMT))))
        nfilters, = unpack(b'<l', f.read(calcsize(b'<l')))
        if nfilters > 0:
            header_fmt = b'<' + b'Q' * nfilters
            bytes = f.read(calcsize(header_fmt))
            filter_lengths = unpack(header_fmt, bytes)
            for fl in filter_lengths:
                filter.filters.append(BloomFilter.fromfile(f, fl))
        else:
            filter.filters = []

        return filter


if __name__ == "__main__":
    import doctest
    doctest.testmod()
