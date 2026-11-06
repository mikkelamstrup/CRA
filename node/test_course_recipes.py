"""Recipe boundary tests complement real guest solution/reset acceptance."""
from uuid import uuid4

import pytest
from test_cleanup import agent, resources


def test_course_flags_are_scoped_and_only_published_in_intended_channel(resources):
    _, command, _, _, _=resources
    command.mission_version=1
    (agent.runtime.SECRETS/'flags.key').write_bytes(b'synthetic-course-key'.ljust(32,b'0'))
    for mission,objective in [('web-trail-dns','find-dns-report'),('access-granted','read-authorized-report')]:
        command.mission_id=mission
        flag=agent.flag_value(command)
        assert agent.objective_id(command)==objective
        pages=agent.target_pages(command)
        if mission=='web-trail-dns':
            assert [p for p,s in pages.items() if flag in s]==['rapporter/rapport-308.txt']
        else:
            assert flag not in ''.join(pages.values())
        command.generation+=1
        assert agent.flag_value(command)!=flag
        command.generation-=1
        command.user_id=uuid4()
        assert agent.flag_value(command)!=flag
    command.mission_id='unapproved-course'
    with pytest.raises(ValueError):agent.target_pages(command)
