"""Measured UI behavior with real synthetic accounts; not a human teaching pilot."""
import json
from pathlib import Path
import sys
import traceback
from playwright.sync_api import sync_playwright,expect
from browser_teaching import login


def main():
    bundle=json.load(sys.stdin)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/cra-browser/chrome-linux64/chrome',chromium_sandbox=True,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
        page.goto('chrome://sandbox');assert 'You are adequately sandboxed.' in page.locator('body').inner_text()
        login(page,bundle['users']['student'])
        expect(page.get_by_role('main')).to_be_visible()
        expect(page.get_by_label('Mine observationer')).to_be_visible()
        page.get_by_label('Mine observationer').fill('Syntetisk udkast som skal bevares.')
        dialogs=[]
        def dismiss(d):dialogs.append(d.type);d.dismiss()
        page.once('dialog',dismiss);page.get_by_role('button',name='? Bed om hjælp').click()
        assert dialogs==['confirm']
        expect(page.get_by_label('Mine observationer')).to_have_value('Syntetisk udkast som skal bevares.')
        page.get_by_role('button',name='Gem notesbog',exact=True).click()
        expect(page.get_by_text('Dine noter er gemt.',exact=True)).to_be_visible()
        page.reload();expect(page.get_by_label('Mine observationer')).to_have_value('Syntetisk udkast som skal bevares.')
        page.keyboard.press('Tab');expect(page.get_by_role('link',name='Spring til indhold')).to_be_focused()
        page.keyboard.press('Enter');expect(page.get_by_role('main')).to_be_focused()
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        assert page.evaluate("""()=>Array.from(document.querySelectorAll('*')).every(el=>{const s=getComputedStyle(el);return s.animationName==='none'&&s.transitionDuration.split(',').every(t=>parseFloat(t)===0);})""")
        assert page.evaluate("document.documentElement.lang==='da'")
        unnamed=page.evaluate("""()=>Array.from(document.querySelectorAll('input,textarea,select')).filter(el=>el.offsetParent!==null&&!el.labels?.length&&!el.getAttribute('aria-label')&&!el.getAttribute('aria-labelledby')).length""")
        assert unnamed==0
        folder=Path('/var/lib/cra-browser/acceptance');folder.mkdir(mode=0o700,exist_ok=True)
        for width in (390,768,1440):
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.evaluate('scrollTo(0,0)')
            assert 0<=page.get_by_role('heading',name='Dit forløb',exact=True).bounding_box()['y']<450
            page.screenshot(path=str(folder/f'accessibility-{width}.png'),full_page=True)
        page.get_by_role('button',name='? Bed om hjælp').click()
        expect(page.get_by_label('Hvad har du brug for hjælp til?')).to_be_visible()
        page.get_by_label('Hvad har du brug for hjælp til?').fill('Syntetisk usendt hjælp')
        page.once('dialog',dismiss);page.get_by_role('button',name='▦ Mit forløb').click()
        expect(page.get_by_label('Hvad har du brug for hjælp til?')).to_have_value('Syntetisk usendt hjælp')
        browser.close();print('PASS: real keyboard/landmark/labels, 390/768/1440 layout, reduced-motion and unsaved-draft navigation',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: '+type(error).__name__+' lines '+','.join(str(f.lineno) for f in traceback.extract_tb(error.__traceback__)),flush=True);sys.exit(1)
