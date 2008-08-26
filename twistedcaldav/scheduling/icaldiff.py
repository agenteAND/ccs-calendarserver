##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

from twistedcaldav.ical import Component
from twistedcaldav.log import Logger
from twistedcaldav.scheduling.itip import iTipGenerator

"""
Class that handles diff'ing two calendar objects.
"""

__all__ = [
    "iCalDiff",
]

log = Logger()

class iCalDiff(object):
    
    def __init__(self, calendar1, calendar2):
        """
        
        @param calendar1:
        @type calendar1:
        @param calendar2:
        @type calendar2:
        """
        
        self.calendar1 = calendar1
        self.calendar2 = calendar2
    
    def organizerDiff(self):
        """
        Diff the two calendars looking for changes that should trigger implicit scheduling if
        changed by an organizer. Basically any change except for anything related to a VALARM.
        """
        
        # Do straight comparison without alarms
        self.calendar1 = self.calendar1.duplicate()
        self.calendar1.removeAlarms()
        self.calendar2 = self.calendar2.duplicate()
        self.calendar2.removeAlarms()

        return self.calendar1 == self.calendar2

    def attendeeMerge(self, attendee):
        """
        Merge the ATTENDEE specific changes with the organizer's view of the attendee's event.
        This will remove any attempt by the attendee to change things like the time or location.
       
        @param attendee: the value of the ATTENDEE property corresponding to the attendee making the change
        @type attendee: C{str}
        """
        
        self.attendee = attendee

        # Do straight comparison without alarms
        self.calendar1 = self.calendar1.duplicate()
        self.calendar1.removeXProperties()
        self.calendar1.attendeesView((attendee,))
        iTipGenerator.prepareSchedulingMessage(self.calendar1)

        self.calendar2 = self.calendar2.duplicate()
        self.calendar2.removeXProperties()
        iTipGenerator.prepareSchedulingMessage(self.calendar2)

        if self.calendar1 == self.calendar2:
            return True, True

        # Need to look at each component and do special comparisons
        
        # Make sure the same VCALENDAR properties match
        if not self._checkVCALENDARProperties():
            return False, False
        
        # Make sure the same VTIMEZONE components appear
        if not self._compareVTIMEZONEs():
            return False, False
        
        # Compare each component instance from the new calendar with each derived
        # component instance from the old one
        return self._compareComponents()
    
    def _checkVCALENDARProperties(self):

        # Get property differences in the VCALENDAR objects
        propdiff = set(self.calendar1.properties()) ^ set(self.calendar2.properties())
        
        # Ignore certain properties
        ignored = ("PRODID", "CALSCALE",)
        propdiff = set([prop for prop in propdiff if prop.name() not in ignored])
        
        result = len(propdiff) == 0
        if not result:
            log.debug("VCALENDAR properties differ: %s" % (propdiff,))
        return result

    def _compareVTIMEZONEs(self):

        # FIXME: clients may re-write timezones so the best we can do is
        # compare TZIDs. That is not ideal as a client could have an old version
        # of a VTIMEZONE and thus could show events at different times than the
        # organizer.
        
        def extractTZIDs(calendar):

            tzids = set()
            for component in calendar.subcomponents():
                if component.name() == "VTIMEZONE":
                    tzids.add(component.propertyValue("TZID"))
            return tzids
        
        tzids1 = extractTZIDs(self.calendar1)
        tzids2 = extractTZIDs(self.calendar2)
        result = tzids1 == tzids2
        if not result:
            log.debug("Different VTIMEZONES: %s %s" % (tzids1, tzids2))
        return result

    def _compareComponents(self):
        
        # First get uid/rid map of components
        def mapComponents(calendar):
            map = {}
            for component in calendar.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                name = component.name()
                uid = component.propertyValue("UID")
                rid = component.getRecurrenceIDUTC()
                map[(name, uid, rid,)] = component
            return map
        
        map1 = mapComponents(self.calendar1)
        set1 = set(map1.keys())
        map2 = mapComponents(self.calendar2)
        set2 = set(map2.keys())

        # All the components in calendar1 must be in calendar2
        result = set1 - set2
        if result:
            log.debug("Missing components from first calendar: %s" % (result,))
            return False, False

        # Now verify that each component in set1 matches what is in set2
        attendee_unchanged = True
        for key, value in map1.iteritems():
            component1 = value
            component2 = map2[key]
            
            nomismatch, no_attendee_change = self._testComponents(component1, component2)
            if not nomismatch:
                return False, False
            attendee_unchanged &= no_attendee_change
        
        # Now verify that each additional component in set2 matches a derived component in set1
        for key in set2 - set1:
            component1 = self.calendar1.deriveInstance(key[2])
            if component1 is None:
                return False, False
            component2 = map2[key]
            
            nomismatch, no_attendee_change = self._testComponents(component1, component2)
            if not nomismatch:
                return False, False
            attendee_unchanged &= no_attendee_change
            
        return True, attendee_unchanged

    def _testComponents(self, comp1, comp2):
        
        assert isinstance(comp1, Component) and isinstance(comp2, Component)
        
        if comp1.name() != comp2.name():
            log.debug("Component names are different: '%s' and '%s'" % (comp1.name(), comp2.name()))
            return False, False
        
        # Only accept a change to this attendee's own ATTENDEE property
        propdiff = set(comp1.properties()) ^ set(comp2.properties())
        for prop in tuple(propdiff):
            # These ones are OK to change
            if prop.name() in (
                "TRANSP",
                "DTSTAMP",
                "CREATED",
                "LAST-MODIFIED",
                "SEQUENCE",
            ):
                propdiff.remove(prop)
                continue
            if prop.name() != "ATTENDEE" or prop.value() != self.attendee:
                log.debug("Component properties are different: %s" % (propdiff,))
                return False, False

        # Compare subcomponents.
        # NB at this point we assume VALARMS have been removed.
        result = set(comp1.subcomponents()) ^ set(comp2.subcomponents())
        if result:
            log.debug("Sub-components are different: %s" % (result,))
            return False, False
        
        return True, len(propdiff) == 0
