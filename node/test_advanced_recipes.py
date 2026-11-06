"""Flag channels and actual course service configuration have fixed scope."""
from test_cleanup import agent, resources


def test_advanced_flags_stay_in_the_intended_channel_and_rotate(resources):
    _,command,_,_,_=resources
    command.mission_version=1
    (agent.runtime.SECRETS/'flags.key').write_bytes(b'synthetic-advanced-key'.ljust(32,b'0'))
    for mission in ['packet-hunter','vulnerable-app','operation-shadow']:
        command.mission_id=mission
        flag=agent.flag_value(command);pages=agent.target_pages(command)
        if mission=='packet-hunter':
            assert [p for p,text in pages.items() if flag in text]==['evidence/trace-505.txt']
        else:assert flag not in ''.join(pages.values())
        command.generation+=1
        assert agent.flag_value(command)!=flag
        command.generation-=1
