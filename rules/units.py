def parse_millicelsius(value, unit='mC'):
    if unit != 'mC' or type(value) is not int:
        raise ValueError('temperature must be an integer in mC')
    return value
