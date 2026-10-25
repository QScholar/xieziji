"""Original line demo font and geometric test fixtures; no vendor data."""
import pathlib
import struct
import zipfile

# Original 4 x 6 line alphabet written for this project.
FONT = {'A': [[(0, 6), (2, 0), (4, 6), (3, 3), (1, 3)]], 'B': [[(0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3), (3, 3), (4, 4), (4, 5), (3, 6), (0, 6)]], 'C': [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5)]], 'D': [[(0, 6), (0, 0), (2, 0), (4, 2), (4, 4), (2, 6), (0, 6)]], 'E': [[(4, 0), (0, 0), (0, 6), (4, 6)], [(0, 3), (3, 3)]], 'F': [[(4, 0), (0, 0), (0, 6)], [(0, 3), (3, 3)]], 'G': [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 3), (2, 3)]], 'H': [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]], 'I': [[(0, 0), (4, 0)], [(2, 0), (2, 6)], [(0, 6), (4, 6)]], 'J': [[(0, 0), (4, 0), (4, 5), (3, 6), (1, 6), (0, 5)]], 'K': [[(0, 0), (0, 6)], [(4, 0), (0, 3), (4, 6)]], 'L': [[(0, 0), (0, 6), (4, 6)]], 'M': [[(0, 6), (0, 0), (2, 3), (4, 0), (4, 6)]], 'N': [[(0, 6), (0, 0), (4, 6), (4, 0)]], 'O': [[(1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0)]], 'P': [[(0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)]], 'Q': [[(1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0)], [(2, 4), (4, 6)]], 'R': [[(0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3, 3), (0, 3)], [(2, 3), (4, 6)]], 'S': [[(4, 1), (3, 0), (1, 0), (0, 1), (0, 2), (1, 3), (3, 3), (4, 4), (4, 5), (3, 6), (1, 6), (0, 5)]], 'T': [[(0, 0), (4, 0)], [(2, 0), (2, 6)]], 'U': [[(0, 0), (0, 5), (1, 6), (3, 6), (4, 5), (4, 0)]], 'V': [[(0, 0), (2, 6), (4, 0)]], 'W': [[(0, 0), (1, 6), (2, 3), (3, 6), (4, 0)]], 'X': [[(0, 0), (4, 6)], [(4, 0), (0, 6)]], 'Y': [[(0, 0), (2, 3), (4, 0)], [(2, 3), (2, 6)]], 'Z': [[(0, 0), (4, 0), (0, 6), (4, 6)]], '.': [[(1.8, 5.7), (2.2, 6)]], ',': [[(2, 5.5), (1.6, 6)]]}
DIGITS = {
 '0': [[(1,0),(3,0),(4,1),(4,5),(3,6),(1,6),(0,5),(0,1),(1,0)]],
 '1': [[(1,1),(2,0),(2,6)],[(0,6),(4,6)]],
 '2': [[(0,1),(1,0),(3,0),(4,1),(4,2),(0,6),(4,6)]],
 '3': [[(0,0),(4,0),(2,3),(4,4),(4,5),(3,6),(0,6)]],
 '4': [[(3,6),(3,0),(0,4),(4,4)]],
 '5': [[(4,0),(0,0),(0,3),(3,3),(4,4),(4,5),(3,6),(0,6)]],
 '6': [[(4,0),(1,0),(0,2),(0,5),(1,6),(3,6),(4,5),(4,4),(3,3),(0,3)]],
 '7': [[(0,0),(4,0),(1,6)]],
 '8': [[(1,0),(3,0),(4,1),(4,2),(3,3),(1,3),(0,2),(0,1),(1,0)],[(1,3),(0,4),(0,5),(1,6),(3,6),(4,5),(4,4),(3,3)]],
 '9': [[(4,3),(1,3),(0,2),(0,1),(1,0),(3,0),(4,1),(4,4),(3,6),(0,6)]],
}
SYMBOLS = {
 '!': [[(2,0),(2,4)],[(1.8,5.8),(2.2,6)]],
 '?': [[(0,1),(1,0),(3,0),(4,1),(4,2),(2,3),(2,4)],[(1.8,5.8),(2.2,6)]],
 '-': [[(0,3),(4,3)]], '_': [[(0,6),(4,6)]],
 ':': [[(1.8,2),(2.2,2)],[(1.8,5),(2.2,5)]],
 '/': [[(0,6),(4,0)]], '+': [[(0,3),(4,3)],[(2,1),(2,5)]],
 '=': [[(0,2),(4,2)],[(0,4),(4,4)]],
}
TEST_CHINESE = '你好世界。中文与英文可以一起排版。'


def glyph_record(character, strokes):
    values=[]; kinds=[]
    for stroke in strokes:
        for i,(x,y) in enumerate(stroke):
            values.extend((x,y)); kinds.append(0 if i==0 else 1)
    return (struct.pack('>HI',ord(character),len(values))
            + struct.pack(f'>{len(values)}f',*values)
            + struct.pack('>I',len(kinds)) + bytes(kinds))


def make_font(path, include_test_chinese=False):
    glyphs={**FONT,**DIGITS,**SYMBOLS}
    # Lowercase uses original small-cap line forms for this demo.
    for char in 'abcdefghijklmnopqrstuvwxyz':
        glyphs[char]=[[(x*.8,y*.8+1.2) for x,y in stroke] for stroke in FONT[char.upper()]]
    glyphs[' ']=[]
    if include_test_chinese:
        for char in TEST_CHINESE:
            glyphs[char]=[[(0,0),(5,0),(5,6),(0,6),(0,0)],[(0,0),(5,6)]]
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as stream:
        stream.write(struct.pack('>I',5))
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for char, strokes in sorted(glyphs.items(),key=lambda item:ord(item[0])):
                entry=zipfile.ZipInfo(str(ord(char)),date_time=(2026,1,1,0,0,0))
                entry.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(entry,glyph_record(char,strokes))
    return len(glyphs)


if __name__=='__main__':
    root=pathlib.Path(__file__).resolve().parents[1]
    print('Demo glyphs:',make_font(root/'gcodeFonts/demo.gfont'))
    print('Test glyphs:',make_font(root/'tests/fixtures/fixture.gfont',True))
