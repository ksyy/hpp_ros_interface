import smach, smach_ros, rospy
from std_msgs.msg import UInt32, Empty, String
from hpp_ros_interface.srv import *
from hpp_ros_interface.msg import *
from hpp_ros_interface.client import HppClient
import std_srvs.srv
from hpp_ros_interface import ros_tools

_outcomes = ["succeeded", "aborted", "preempted"]

class InitializePath(smach.State):
    hppTargetPubDict = {
            "read_path": [ UInt32, 1 ],
            "read_subpath": [ ReadSubPath, 1 ],
            }
    hppTargetSrvDict = {
            "reset_topics": [ std_srvs.srv.Empty, ],
            "add_center_of_mass": [ SetString, ],
            "add_operational_frame": [ SetString, ],
            "add_center_of_mass_velocity": [ SetString, ],
            "add_operational_frame_velocity": [ SetString, ],
            }

    def __init__(self):
        super(InitializePath, self).__init__(
                outcomes = _outcomes,
                input_keys = [ "pathId", "times", "currentSection" ],
                output_keys = [ "transitionId", "currentSection" ],
                )

        self.targetSrv = ros_tools.createServices (self, "/hpp/target", self.hppTargetSrvDict, serve=False)
        self.targetPub = ros_tools.createTopics (self, "/hpp/target", self.hppTargetPubDict, subscribe=False)
        self.hppclient = HppClient (False)

    def execute (self, userdata):
        # TODO makeSot
        # self.targetSrv["reset_topics"](std_srvs.srv.Empty())
        # # TODO Set the topics specifying the targets
        # self.targetSrv["add_center_of_mass"](SetStringRequest("name"))
        # self.targetSrv["add_center_of_mass_velocity"](SetStringRequest("name"))
        # # ...

        userdata.currentSection += 1
        if userdata.currentSection + 1 >= len(userdata.times):
            # TODO Change SOT to id -1
            return _outcomes[0]
        start = userdata.times[userdata.currentSection]
        length = userdata.times[userdata.currentSection + 1] - start

        hpp = self.hppclient._hpp()
        manip = self.hppclient._manip()
        print userdata.pathId, start + length / 2
        # rospy.sleep(3)
        userdata.transitionId = manip.problem.edgeAtParam(userdata.pathId, start + length / 2)
        # rospy.sleep(3)
        # userdata.transitionId = manip.problem.edgeAtParam(userdata.pathId, start)

        self.targetPub["read_subpath"].publish (ReadSubPath (userdata.pathId, start, length))
        rospy.sleep(3)
        return _outcomes[2]

class PlayPath (smach.State):
    hppTargetPubDict = {
            "publish": [ Empty, 1 ],
            }
    subscribersDict = {
            "sot_hpp": {
                "error": [ String, "handleError" ],
                "interrupt": [ String, "handleInterrupt" ],
                },
            "hpp" : {
                "target": {
                    "publish_done": [ Empty, "handleFinished" ]
                    }
                }
            }
    serviceProxiesDict = {
            'sot': {
                'plug_sot': [ PlugSot, ]
                }
            }

    def __init__(self):
        super(PlayPath, self).__init__(
                outcomes = _outcomes,
                input_keys = [ "transitionId", ],
                output_keys = [ ])

        self.targetPub = ros_tools.createTopics (self, "/hpp/target", self.hppTargetPubDict, subscribe=False)
        self.subscribers = ros_tools.createTopics (self, "", self.subscribersDict, subscribe=True)
        self.serviceProxies = ros_tools.createServices (self, "", PlayPath.serviceProxiesDict, serve=False)

        self.done = False
        self.error = None
        self.interruption = None

    def handleError (self, msg):
        self.error = msg.data

    def handleInterrupt (self, msg):
        self.interruption = msg.data
        rospy.loginfo(str(msg.data))
        self.done = True

    def handleFinished (self, msg):
        self.done = True

    def execute(self, userdata):
        # TODO Check that there the current SOT and the future SOT are compatible ?
        status = self.serviceProxies['sot']['plug_sot'](userdata.transitionId)
        if not status.success:
            rospy.logerr(status.msg)
            return _outcomes[1]

        self.done = False
        self.targetPub["publish"].publish(Empty())
        # Wait for errors or publish done
        rate = rospy.Rate (1000)
        while not self.done:
            if self.error is not None:
                # TODO handle error
                rospy.logerr(str(self.error))
                self.error = None
                return _outcomes[1]
            rate.sleep()
        if self.interruption is not None:
            rospy.logerr(str(self.interruption))
            self.interruption = None
            return _outcomes[2]
        return _outcomes[0]

class WaitForInput(smach.State):
    serviceProxiesDict = {
            'sot': {
                'request_hpp_topics': [ std_srvs.srv.Trigger, ]
                },
            'hpp': {
                'target': {
                    "reset_topics": [ std_srvs.srv.Empty, ],
                    }
                }
            }

    def __init__(self):
        super(WaitForInput, self).__init__(
                outcomes = [ "succeeded", "aborted" ],
                input_keys = [ ],
                output_keys = [ "pathId", "times", "currentSection" ],
                )

        self.services = ros_tools.createServices (self, "", self.serviceProxiesDict, serve = False)
        self.hppclient = HppClient (False)

    def execute (self, userdata):
        res = rospy.wait_for_message ("/sm_sot_hpp/start_path", UInt32)
        pid = res.data
        rospy.loginfo("Requested to start path " + str(pid))
        userdata.pathId = pid
        try:
            hpp = self.hppclient._hpp()
            qs, ts = hpp.problem.getWaypoints(pid)
            userdata.times = ts
            userdata.currentSection = -1
            self.services['hpp']['target']['reset_topics']()
            self.services['sot']['request_hpp_topics']()
            # TODO check that qs[0] and the current robot configuration are
            # close
        except Exception, e:
            rospy.logerr("Failed " + str(e))
            return "aborted"
        return "succeeded"

def makeStateMachine():
    sm = smach.StateMachine (outcomes = _outcomes)

    with sm:
        smach.StateMachine.add ('WaitForInput', WaitForInput(),
                transitions = {
                    "succeeded": 'Init',
                    "aborted": "WaitForInput" },
                remapping = {
                    "pathId": "pathId",
                    "times": "times",
                    "currentSection": "currentSection",
                    })
        smach.StateMachine.add ('Init', InitializePath(),
                transitions = {
                    "succeeded": "WaitForInput",
                    "aborted": "aborted",
                    "preempted": 'Play'},
                remapping = {
                    "pathId": "pathId",
                    "times": "times",
                    "transitionId": "transitionId",
                    "currentSection": "currentSection",
                    })
        smach.StateMachine.add ('Play', PlayPath(),
                transitions = {
                    "succeeded": 'Init',
                    "aborted": "WaitForInput",
                    "preempted": "WaitForInput"},
                remapping = {
                    "transitionId": "transitionId",
                    })

    sm.set_initial_state(["WaitForInput"])

    sis = smach_ros.IntrospectionServer('sm_sot_hpp', sm, '/SM_SOT_HPP')
    return sm, sis
