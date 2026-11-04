"""Anvil block-state decoding for saved-world surface tiles."""
import math

AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}

def state_index(data, index, bits, padded=True):
    mask = (1 << bits) - 1
    if padded:
        per_long = 64 // bits
        return ((data[index // per_long] & ((1 << 64) - 1)) >> ((index % per_long) * bits)) & mask
    bit = index * bits; word, shift = divmod(bit, 64)
    value = (data[word] & ((1 << 64) - 1)) >> shift
    if shift + bits > 64:
        value |= (data[word + 1] & ((1 << 64) - 1)) << (64 - shift)
    return value & mask

def surface(chunk, max_y):
    """Yield actual visible (x,z,height,block) columns, supporting old/new Anvil layouts."""
    version = chunk.get('DataVersion', 0)
    chunk = chunk.get('Level', chunk)
    sections = chunk.get('sections', chunk.get('Sections', []))
    sections = sorted(sections, key=lambda s: s.get('Y', -99), reverse=True)
    result = {}
    for section in sections:
        sy = section.get('Y', -99) * 16
        if sy > max_y: continue
        states = section.get('block_states', {})
        palette = states.get('palette', section.get('Palette', []))
        data = states.get('data', section.get('BlockStates', []))
        if not palette: continue
        bits = max(4, (len(palette) - 1).bit_length())
        for z in range(16):
            for x in range(16):
                if (x,z) in result: continue
                for y in range(min(15, max_y - sy), -1, -1):
                    index = y * 256 + z * 16 + x
                    pi = state_index(data, index, bits, version >= 2529) if len(palette) > 1 else 0
                    name = palette[pi].get('Name', 'minecraft:air')
                    if name not in AIR:
                        result[x,z] = (sy+y, name.removeprefix('minecraft:'))
                        break
    return result
