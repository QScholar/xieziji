#include "controller.hpp"
#include "picosha2.h"
#include <array>
#include <cstdio>
#include <iostream>
#include <mutex>
#include <stdexcept>

using namespace plotter;
using namespace std::chrono_literals;
namespace {
void expect(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void rejects(F fn) { bool rejected=false; try { fn(); } catch (const std::exception&) { rejected=true; } expect(rejected,"Expected rejection"); }
struct Trace {
    std::mutex mutex;
    std::vector<std::string> sent;
    std::atomic<bool> alarm{false}, bad_config{false}, draining{false}, wrong_final{false};
    std::array<double,3> work_offset{0,0,0};
    double tool_offset=0, temporary_z=0;
    std::atomic<bool> hold_alarm{false}, reset_alarm{false}, reset_moved{false}, return_z_wrong{false}, return_wait{false};
    std::atomic<bool> reset_seen{false}, hold_complete{false}, return_z_seen{false}, return_xy_seen{false};
    std::atomic<bool> unsolicited_boot{false};
    int hold_deceleration=0;
    bool contact_mode=false, four_axes=false, alternate_firmware=false;
    std::string startup_state;
    size_t count() { std::lock_guard<std::mutex> lock(mutex); return sent.size(); }
    bool contains(const std::string& value) { std::lock_guard<std::mutex> lock(mutex); return std::find(sent.begin(),sent.end(),value)!=sent.end(); }
};
class Fake final : public Serial {
    std::shared_ptr<Trace> trace;
    std::unique_ptr<Serial> delegate=simulated_serial();
    bool pending_alarm=false, final=false;
public:
    explicit Fake(std::shared_ptr<Trace> t):trace(std::move(t)){}
    void write(const std::string& raw) override {
        { std::lock_guard<std::mutex> lock(trace->mutex); trace->sent.push_back(raw); }
        if (raw=="\x18") {
            expect(trace->hold_complete || trace->startup_state=="Door:0" || trace->startup_state=="Hold:0","Reset before physical hold completed");
            trace->startup_state.clear();trace->reset_seen=true;final=false;
        }
        if (trace->reset_seen && raw=="$J=G21 G90 G53 Z0 F600\n") trace->return_z_seen=true;
        if (trace->reset_seen && raw.rfind("$J=G21 G90 G53 X",0)==0) trace->return_xy_seen=true;
        if (trace->alarm && raw=="G1 Z10 F3000\n") { pending_alarm=true; return; }
        if (raw=="G1 X0 Y0 F3000\n") final=true;
        delegate->write(raw);
    }
    std::string read_line() override {
        if(trace->unsolicited_boot.exchange(false))return "Grbl 1.3a ['$' for help]";
        if (pending_alarm) { pending_alarm=false; return "ALARM:1"; }
        auto line=delegate->read_line();
        if(!trace->startup_state.empty() && line.rfind("<Idle|",0)==0)line.replace(1,4,trace->startup_state);
        if(trace->alternate_firmware && line.rfind("[VER:",0)==0) return "[VER:1.3a.20211103:4axis dual Y]";
        if(trace->four_axes && line.rfind("<",0)==0) {
            auto at=line.find("|FS:");if(at!=std::string::npos)line.insert(at,",0.000");
        }
        if (trace->reset_seen && trace->reset_alarm && line.rfind("Grbl ",0)==0) return "ALARM:1";
        if (line.rfind("<Hold:0|",0)==0) {
            if (trace->hold_alarm) return "ALARM:1";
            if (trace->hold_deceleration>0) { --trace->hold_deceleration; line.replace(1,6,"Hold:1"); }
            else trace->hold_complete=true;
        }
        if (trace->reset_seen && trace->reset_moved && line.rfind("<Idle|",0)==0) return "<Idle|MPos:99,99,0|FS:0,0>";
        if (trace->return_z_seen && trace->return_z_wrong && line.rfind("<Idle|",0)==0) return "<Idle|MPos:1,2,5|FS:0,0>";
        if (trace->return_xy_seen && trace->return_wait && line.rfind("<Idle|",0)==0) line.replace(1,4,"Run");
        if (line.rfind("[G54:",0)==0) return "[G54:"+std::to_string(trace->work_offset[0])+","+std::to_string(trace->work_offset[1])+","+std::to_string(trace->work_offset[2])+(trace->four_axes?",0.000]":"]");
        if (line.rfind("[G92:",0)==0) {
            double x=0,y=0,z=0;
            expect(std::sscanf(line.c_str(),"[G92:%lf,%lf,%lf]",&x,&y,&z)==3,"Invalid simulated offset");
            // The delegate stores the combined XY origin. GRBL reports G92
            // relative to G54, so expose the individual offsets separately.
            return "[G92:"+std::to_string(x-trace->work_offset[0])+","+std::to_string(y-trace->work_offset[1])+","+std::to_string(trace->contact_mode?z-trace->work_offset[2]:trace->temporary_z)+(trace->four_axes?",0.000]":"]");
        }
        if (line.rfind("[TLO:",0)==0) return "[TLO:"+std::to_string(trace->tool_offset)+"]";
        if (trace->bad_config && line=="$3=7") return "$3=0";
        if (final && trace->draining && line.rfind("<Idle",0)==0) line.replace(1,4,"Run");
        if (final && trace->wrong_final && line.rfind("<Idle",0)==0) return "<Idle|MPos:12,0,0|FS:0,0>";
        return line;
    }
};
struct Fixture {
    fs::path path=fs::current_path()/".controller-testdata"/("run-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::shared_ptr<Trace> trace=std::make_shared<Trace>();
    std::unique_ptr<Controller> c;
    std::string id=std::string(32,'a');
    Fixture() { c=std::make_unique<Controller>(path,true,[this](const auto&){return std::make_unique<Fake>(trace);}); c->call("connect",{{"port","SIMULATOR"}}); }
    ~Fixture() { c.reset(); std::error_code ec; fs::remove_all(path,ec); }
    std::string gcode(int n=2) {
        std::string text="G21\nG90\nG94\nG1 Z0 F3000\nG92 X0 Y0\nG1 Z10 F3000\n";
        for (int i=0;i<n;++i) text+="G1 X1.000 Y2.000 F1800\n";
        return text+"G1 Z0 F3000\nG1 X0 Y0 F3000\n";
    }
    Json meta(const std::string& text) { return {{"sha256",picosha2::hash256_hex_string(text)}, {"commands",std::count(text.begin(),text.end(),'\n')}, {"machine_paper",{210,297}}, {"machine_page_bounds",{0,0,210,297}}}; }
    void stage(const std::string& text) {
        c->upload(id,"begin",meta(text)); c->upload(id,"chunk",{{"offset",0},{"content",text}}); c->upload(id,"seal",Json::object());
    }
    void start() { c->call("start",{{"job_id",id},{"origin_confirmed",true}}); }
    Json wait(const std::string& phase) {
        auto end=std::chrono::steady_clock::now()+8s;
        while (std::chrono::steady_clock::now()<end) { auto s=c->state(); if (s["phase"]==phase) return s; if (s["phase"]=="failed" && phase!="failed") throw std::runtime_error(s.dump()); std::this_thread::sleep_for(5ms); }
        throw std::runtime_error("State timeout: "+c->state().dump());
    }
};
void realtime_speed() {
    Fixture f;
    rejects([&]{f.c->call("speed",{{"percent",80},{"job_id",f.id}});});
    f.stage(f.gcode(300)); f.start(); f.c->call("pause",Json::object());
    auto set_speed = [&](int target) {
        auto result=f.c->call("speed",{{"percent",target},{"job_id",f.id}});
        expect(result["feed_override_requested"]==target,"Target speed not recorded");
        auto deadline=std::chrono::steady_clock::now()+3s;
        while (std::chrono::steady_clock::now()<deadline) {
            auto s=f.c->state();
            if (s["feed_override"]==target) { expect(s["phase"]=="paused","Speed resumed a paused task"); return; }
            std::this_thread::sleep_for(5ms);
        }
        throw std::runtime_error("Speed feedback timeout: "+f.c->state().dump());
    };
    for (int target : {10,200,137,53,100}) set_speed(target);
    for (auto bad : {Json(9),Json(201),Json(50.5),Json(true),Json("80"),Json(nullptr)})
        rejects([&]{f.c->call("speed",{{"percent",bad},{"job_id",f.id}});});
    rejects([&]{f.c->call("speed",{{"percent",80},{"job_id","old-job"}});});
    expect(!f.trace->contains("\x18"),"Speed reset the machine");
    f.c->call("resume",Json::object());f.wait("complete");
    f.id=std::string(32,'d'); auto high=f.gcode(300);
    size_t offset=0;
    while ((offset=high.find("F1800",offset))!=std::string::npos) { high.replace(offset,5,"F12000");offset+=6; }
    f.stage(high);f.start();f.c->call("pause",Json::object());
    expect(f.c->state()["speed_max_percent"]==100,"High speed multiplier exceeds 12000 mm/min");
    rejects([&]{f.c->call("speed",{{"percent",101},{"job_id",f.id}});});
    set_speed(80);f.c->call("resume",Json::object());f.wait("complete");
    rejects([&]{f.c->call("speed",{{"percent",80},{"job_id",f.id}});});
    f.id=std::string(32,'e');
    for (size_t p=0;(p=high.find("F12000",p))!=std::string::npos;p+=6) high.replace(p,6,"F10000");
    f.stage(high);f.start();f.c->call("pause",Json::object());
    expect(f.c->state()["speed_max_percent"]==120,"10000 base must allow 120 percent");
    set_speed(120);
    rejects([&]{f.c->call("speed",{{"percent",121},{"job_id",f.id}});});
    f.c->call("resume",Json::object());f.wait("complete");
    // A new job restores the default, including when the previous job was slow.
    f.id=std::string(32,'b');f.stage(f.gcode(300));f.start();f.c->call("pause",Json::object());set_speed(25);
    f.c->call("resume",Json::object());f.wait("complete");
    f.id=std::string(32,'c');f.stage(f.gcode(300));f.start();f.c->call("pause",Json::object());
    expect(f.c->state()["feed_override_requested"]==100,"New job inherited previous speed");
    f.c->call("resume",Json::object());f.wait("complete");
}
void upload_validation() {
    Fixture f; auto text=f.gcode(); auto m=f.meta(text);
    f.c->upload(f.id,"begin",m); f.c->upload(f.id,"chunk",{{"offset",0},{"content",text}});
    f.c->upload(f.id,"chunk",{{"offset",0},{"content",text}}); // same chunk retry
    rejects([&]{f.c->upload(f.id,"chunk",{{"offset",0},{"content","G20"}});});
    rejects([&]{f.c->upload("../bad","begin",m);});
    rejects([&]{f.c->upload(f.id,"begin",Json::object());});
    f.c->upload(f.id,"seal",Json::object());
    f.start(); auto s=f.wait("complete"); expect(s["acknowledged"]==m["commands"],"Wrong line count");
    auto count=f.trace->count(); f.start(); expect(count==f.trace->count(),"Duplicate start replayed motion");
    rejects([&]{f.c->upload(f.id,"chunk",{{"offset",0},{"content",text}});});
    f.c.reset(); f.c=std::make_unique<Controller>(f.path,true); rejects([&]{f.start();});
}
void checksum_and_bounds() {
    for (int kind=0;kind<4;++kind) {
        Fixture f; auto text=f.gcode();
        if (kind==1) text.replace(text.find("X1.000"),6,"X99999");
        if (kind==2) text.replace(text.find("G21"),3,"G20");
        if (kind==3) text.replace(text.find("Z10"),3,"Z99");
        auto m=f.meta(text); if (kind==0) m["sha256"]=std::string(64,'0');
        f.c->upload(f.id,"begin",m); f.c->upload(f.id,"chunk",{{"offset",0},{"content",text}});
        rejects([&]{f.c->upload(f.id,"seal",Json::object());}); rejects([&]{f.start();});
        expect(!f.trace->contains("G21\n"),"Invalid task moved the machine");
    }
}
void pause_stop_and_lock() {
    Fixture f; f.stage(f.gcode(500)); f.start();
    rejects([&]{f.c->call("jog",{{"axis","X"},{"distance",1},{"feed",300}});});
    f.c->call("pause",Json::object()); std::this_thread::sleep_for(30ms); auto ack=f.c->state()["acknowledged"];
    std::this_thread::sleep_for(80ms); expect(f.c->state()["acknowledged"]==ack,"Task advanced while paused");
    f.c->call("resume",Json::object()); f.c->call("stop",Json::object()); f.wait("stopped");
    expect(f.trace->contains("!") && f.trace->contains("\x18"),"Stop did not hold then reset");
    rejects([&]{f.c->call("resume",Json::object());}); f.start(); expect(f.c->state()["phase"]=="stopped","Stopped task replayed");
}
void stop_returns_xyz() {
    for (bool paused : {false,true}) {
        Fixture f; f.trace->work_offset={13,17,0}; f.trace->hold_deceleration=3;
        f.c->call("jog",{{"axis","X"},{"distance",14},{"feed",300}}); f.wait("idle");
        f.c->call("jog",{{"axis","Y"},{"distance",-16},{"feed",300}}); f.wait("idle");
        f.stage(f.gcode(500)); f.start();
        auto deadline=std::chrono::steady_clock::now()+3s;
        while (f.c->state()["acknowledged"].get<int>()<6 && std::chrono::steady_clock::now()<deadline) std::this_thread::sleep_for(2ms);
        if (paused) f.c->call("pause",Json::object());
        f.c->call("stop",Json::object()); f.c->call("stop",Json::object());
        auto s=f.wait("stopped");
        expect(s["position"]==Json::array({14.,-16.,0.}) && s["connected"]==true && s["returned_to_origin"]==true,"Stop did not return XYZ and retain connection");
        expect(s["acknowledged"].get<int>()<s["total"].get<int>(),"Stop completed the remaining task");
        std::vector<std::string> sent; { std::lock_guard<std::mutex> lock(f.trace->mutex); sent=f.trace->sent; }
        auto hold=std::find(sent.begin(),sent.end(),"!");
        auto reset=std::find(hold,sent.end(),"\x18");
        auto z=std::find(reset,sent.end(),"$J=G21 G90 G53 Z0 F600\n");
        auto xy=std::find(z,sent.end(),"$J=G21 G90 G53 X14.000 Y-16.000 F1000\n");
        expect(hold<reset && reset<z && z<xy && xy!=sent.end(),"Stop recovery order is incorrect");
        expect(std::none_of(hold,sent.end(),[](const auto& raw){return raw.rfind("G1 ",0)==0;}),"Original path sent after stop");
        expect(std::count(sent.begin(),sent.end(),"\x18")==1,"Duplicate stop reset recovery twice");
        f.c->call("check",Json::object());
        f.c->call("disconnect",Json::object());
    }
    for (int fault=0;fault<4;++fault) {
        Fixture f; f.stage(f.gcode(500)); f.start();
        if (fault==0) f.trace->reset_alarm=true;
        if (fault==1) f.trace->reset_moved=true;
        if (fault==2) f.trace->return_z_wrong=true;
        if (fault==3) f.trace->hold_alarm=true;
        f.c->call("stop",Json::object()); f.wait("failed");
        expect(!f.trace->return_xy_seen,"Unsafe XY return sent after recovery failure");
        if (fault<2) expect(!f.trace->return_z_seen,"Motion sent after invalid reset");
        if (fault==3) expect(!f.trace->reset_seen && !f.trace->return_z_seen,"Recovery reset or moved after a hold alarm");
        expect(!f.trace->contains("$X\n"),"Recovery automatically unlocked an alarm");
    }
    { Fixture f; f.trace->return_wait=true; f.stage(f.gcode(500)); f.start(); f.c->call("stop",Json::object());
      f.wait("returning"); std::this_thread::sleep_for(50ms);
      expect(f.c->state()["phase"]=="returning","Stop completed before physical return");
      f.trace->return_wait=false; auto s=f.wait("stopped");
      expect(s["returned_to_origin"]==true && s["position"]==Json::array({0.,0.,0.}),"Stop did not wait for the actual return"); }
    { Fixture f; f.trace->return_wait=true; f.stage(f.gcode(500)); f.start(); f.c->call("stop",Json::object());
      f.wait("returning"); std::this_thread::sleep_for(50ms);
      expect(f.c->state()["phase"]=="returning","Stop completed before physical return");
      rejects([&]{f.c->call("jog",{{"axis","X"},{"distance",1},{"feed",300}});});
      f.c->call("stop",Json::object()); auto s=f.wait("stopped");
      expect(s["returned_to_origin"]==false && s["connected"]==false,"Cancelling return reported a successful origin return");
      expect(f.trace->contains("\x85"),"Cancelling return did not cancel the jog"); }
}
void alarm_and_configuration() {
    { Fixture f; f.trace->alarm=true; f.stage(f.gcode()); f.start(); f.wait("failed"); expect(f.trace->contains("!"),"Alarm did not request hold"); expect(!f.trace->contains("G1 X1.000 Y2.000 F1800\n"),"Motion sent after alarm"); }
    { Fixture f; f.trace->bad_config=true; f.stage(f.gcode()); rejects([&]{f.start();}); expect(!f.trace->contains("G21\n"),"Bad configuration moved machine"); }
    { Fixture f; f.trace->wrong_final=true; f.stage(f.gcode()); f.start(); f.wait("failed"); }
}
void physical_idle_and_manual() {
    { Fixture f; f.trace->draining=true; f.stage(f.gcode()); f.start(); f.wait("draining"); std::this_thread::sleep_for(150ms); expect(f.c->state()["phase"]=="draining","Completed before physical Idle"); f.trace->draining=false; f.wait("complete"); }
    { Fixture f;
      rejects([&]{f.c->call("jog",{{"axis","Z"},{"distance",1},{"feed",300}});});
      rejects([&]{f.c->call("jog",{{"axis","X"},{"distance",true},{"feed",300}});});
      f.c->call("jog",{{"axis","X"},{"distance",1},{"feed",300}}); f.wait("idle");
      for (int i=0;i<2;++i) { f.c->call("pen",{{"position","down"}}); f.wait("idle"); expect(f.c->state()["pen_top"]==true,"Pen did not return to top"); }
      auto count=f.trace->count(); f.c->call("pen",{{"position","up"}}); expect(f.trace->count()==count+1,"Repeated pen up sent motion");
      f.stage(f.gcode()); f.start(); f.wait("complete"); expect(f.c->state()["position"][0]==1,"Task did not return to its physical origin"); }
}
void immediate_controls_after_completion() {
    Fixture f;
    for (int i=0;i<12;++i) {
        f.id=std::string(31,'b')+"0123456789ab"[i];
        f.stage(f.gcode(1)); f.start();
        auto end=std::chrono::steady_clock::now()+8s;
        while (f.c->state()["phase"]!="complete" && std::chrono::steady_clock::now()<end) std::this_thread::yield();
        expect(f.c->state()["phase"]=="complete","Completion publication timed out");
        // Do not sleep: the worker can still be persisting its final state.
        f.c->call("disconnect",Json::object());
        expect(f.c->state()["phase"]=="disconnected","Disconnect after completion failed");
        f.c->call("connect",{{"port","SIMULATOR"}});
    }
}
void work_coordinate_offsets() {
    for (double sign : {1.,-1.}) {
        Fixture f; f.trace->work_offset={13*sign,17*sign,0};
        f.c->call("jog",{{"axis","X"},{"distance",1},{"feed",300}}); f.wait("idle");
        f.c->call("jog",{{"axis","Y"},{"distance",-2},{"feed",300}}); f.wait("idle");
        auto count=f.trace->count(); f.c->call("check",Json::object());
        expect(f.trace->count()==count+5,"Read-only check sent unexpected commands");
        for (int i=0;i<2;++i) {
            f.id=std::string(31,'c')+char('0'+i);
            f.stage(f.gcode()); f.start(); auto s=f.wait("complete");
            expect(s["position"]==Json::array({1.,-2.,0.}),"Offset task did not return to its physical origin");
        }
    }
    for (int kind=0;kind<3;++kind) {
        Fixture f;
        if (kind==0) f.trace->work_offset[2]=2;
        if (kind==1) f.trace->tool_offset=1;
        if (kind==2) f.trace->temporary_z=1;
        f.stage(f.gcode()); rejects([&]{f.start();});
        expect(!f.trace->contains("G21\n"),"Invalid Z/tool offset sent task commands");
        expect(!fs::exists(f.path/f.id/"consumed"),"Rejected offset consumed the task");
    }
    { Fixture f; f.trace->work_offset={13,17,0}; f.trace->wrong_final=true;
      f.stage(f.gcode()); f.start(); f.wait("failed"); }
}
}
void device_control_profiles() {
    Fixture f;
    f.c->call("disconnect",Json::object());
    Json control={{"controller_type","grbl"},{"pen_up",-1.25},{"pen_down",-3.5},{"pen_feed",200}};
    f.c->call("connect",{{"port","SIMULATOR"},{"device_control",control}});
    f.c->call("pen",{{"position","up"}});f.wait("idle");
    expect(f.c->state()["pen_top"]==true,"Custom up Z was not recognized");
    f.c->call("pen",{{"position","down"}});f.wait("idle");
    expect(f.trace->contains("$J=G21 G90 G53 Z-3.5 F200\n"),"Custom down Z not sent");
    expect(f.trace->contains("$J=G21 G90 G53 Z-1.25 F200\n"),"Custom up Z not sent");
    auto text=f.gcode();
    for (size_t p=0;(p=text.find("Z10",p))!=std::string::npos;p+=5)text.replace(p,3,"Z-3.5");
    for (size_t p=0;(p=text.find("Z0",p))!=std::string::npos;p+=6)text.replace(p,2,"Z-1.25");
    auto meta=f.meta(text);meta["device_control"]=control;meta["device_port"]="SIMULATOR";
    f.c->upload(f.id,"begin",meta);f.c->upload(f.id,"chunk",{{"offset",0},{"content",text}});f.c->upload(f.id,"seal",Json::object());
    f.start();f.wait("complete");
    expect(std::abs(f.c->state()["position"][2].get<double>()+1.25)<.01,"Job did not return to configured up Z");
    f.id=std::string(32,'b');meta["device_port"]="OTHER";
    f.c->upload(f.id,"begin",meta);f.c->upload(f.id,"chunk",{{"offset",0},{"content",text}});f.c->upload(f.id,"seal",Json::object());
    rejects([&]{f.start();});
    f.c->call("disconnect",Json::object());
    f.c->call("connect",{{"port","SIMULATOR"}});
    expect(f.c->state()["device_control"]["pen_up"]==0,"Next machine inherited custom Z");
}
void paper_contact_startup() {
    auto path=fs::current_path()/".controller-testdata"/"contact-startup";
    fs::create_directories(path);
    auto trace=std::make_shared<Trace>();trace->contact_mode=true;trace->four_axes=true;trace->alternate_firmware=true;
    trace->work_offset={-251,-1,18};
    Controller c(path,true,[trace](const auto&){return std::make_unique<Fake>(trace);},"",true);
    auto wait=[&](const std::string& phase){auto end=std::chrono::steady_clock::now()+8s;while(std::chrono::steady_clock::now()<end){auto s=c.state();if(s["phase"]==phase)return s;if(s["phase"]=="failed")throw std::runtime_error(s.dump());std::this_thread::sleep_for(5ms);}throw std::runtime_error("Contact test timeout");};
    c.call("connect",{{"port","SIMULATOR"}});
    expect(c.state()["pen"]=="unknown" && c.state()["calibrated"]==false,"Contact machine must not inherit a reference");
    auto count=trace->count();
    rejects([&]{c.call("pen",{{"position","up"}});});
    rejects([&]{c.call("jog",{{"axis","X"},{"distance",1},{"feed",100}});});
    rejects([&]{c.call("prepare",{{"power_cycled",true},{"pen_on_paper",true}});});
    expect(trace->count()==count,"Unconfirmed preparation sent a command");
    Json confirmation={{"power_cycled",true},{"pen_on_paper",true},{"bottom_confirmed",true}};
    c.call("prepare",confirmation);auto s=wait("idle");
    expect(s["calibrated"]==true && s["logical_z"]==0 && s["pen_top"]==true,"Top reference not established");
    expect(trace->contains("$J=G21 G91 Z-13.5 F100\n") && trace->contains("G92 X0 Y0 Z0\n"),"Preparation sequence missing");
    rejects([&]{c.call("prepare",confirmation);});
    c.call("pen",{{"position","down"}});wait("idle");
    expect(trace->contains("$J=G21 G90 G53 Z0 F100\n") && trace->contains("$J=G21 G90 G53 Z-13.5 F100\n"),"Manual pen did not use session reference");
    std::string text="G21\nG90\nG94\nG1 Z0 F3000\nG92 X0 Y0\nG1 Z13.5 F100\nG1 X1 Y2 F100\nG1 Z0 F3000\nG1 X0 Y0 F3000\n";
    Json meta={{"sha256",picosha2::hash256_hex_string(text)},{"commands",std::count(text.begin(),text.end(),'\n')},{"machine_paper",{210,297}},{"machine_page_bounds",{0,0,210,297}},{"device_control",s["device_control"]}};
    auto id=std::string(32,'c');
    c.upload(id,"begin",meta);c.upload(id,"chunk",{{"offset",0},{"content",text}});c.upload(id,"seal",Json::object());
    c.call("start",{{"job_id",id},{"origin_confirmed",true}});wait("complete");
    expect(trace->contains("G53 G1 Z0 F100\n") && trace->contains("G53 G1 Z-13.5 F3000\n"),"Job Z was not translated into machine coordinates");
    expect(c.state()["logical_z"]==0,"Job did not return to logical Z0");
    // Stopping resets the firmware's G92; recovery must restore the session
    // Z frame before raising and returning, rather than moving 13.5 mm too far.
    std::string long_text="G21\nG90\nG94\nG1 Z0 F3000\nG92 X0 Y0\nG1 Z13.5 F100\n";
    for(int i=0;i<300;++i)long_text+="G1 X1 Y2 F100\n";
    long_text+="G1 Z0 F3000\nG1 X0 Y0 F3000\n";
    meta["sha256"]=picosha2::hash256_hex_string(long_text);meta["commands"]=std::count(long_text.begin(),long_text.end(),'\n');
    id=std::string(32,'d');c.upload(id,"begin",meta);c.upload(id,"chunk",{{"offset",0},{"content",long_text}});c.upload(id,"seal",Json::object());
    c.call("start",{{"job_id",id},{"origin_confirmed",true}});c.call("pause",Json::object());c.call("stop",Json::object());s=wait("stopped");
    expect(s["returned_to_origin"]==true && s["calibrated"]==true && s["logical_z"]==0,"Stop lost the paper-contact reference");
    // A real power/reset event must instead invalidate the reference.
    trace->unsolicited_boot=true;
    rejects([&]{c.call("pen",{{"position","up"}});});
    expect(c.state()["calibrated"]==false,"Unsolicited firmware restart kept calibration");
    c.call("disconnect",Json::object());c.call("connect",{{"port","SIMULATOR"}});
    expect(c.state()["calibrated"]==false && c.state()["logical_z"].is_null(),"Reconnect reused stale calibration");
    rejects([&]{c.call("pen",{{"position","down"}});});
    c.call("disconnect",Json::object());
    std::error_code ec;fs::remove_all(path,ec);
}
void stationary_startup_reset() {
    for(const auto& initial:{"Door:0","Hold:0","Door:1","Door:2","Hold:1","Run"}){
        auto trace=std::make_shared<Trace>();trace->startup_state=initial;
        auto path=fs::current_path()/".controller-testdata"/("startup-"+std::string(initial).substr(0,4));
        {
            Controller c(path,true,[trace](const auto&){return std::make_unique<Fake>(trace);},"",true);
            bool stationary=std::string(initial)=="Door:0" || std::string(initial)=="Hold:0";
            if(stationary){auto s=c.call("connect",{{"port","SIMULATOR"}});expect(s["connected"]==true && s["calibrated"]==false && s["startup_reset"]==true,"Stationary suspension did not reconnect uncalibrated");}
            else rejects([&]{c.call("connect",{{"port","SIMULATOR"}});});
            expect(trace->contains("\x18")==stationary,"Reset an unsafe startup state or missed stationary reset");
            expect(!trace->contains("~"),"Startup resumed an old task");
            for(auto& raw:trace->sent)expect(raw.rfind("$J=",0)!=0 && raw.rfind("G1 ",0)!=0,"Connection sent movement");
        }
        std::error_code ec;fs::remove_all(path,ec);
    }
    // The original machine keeps its strict Idle requirement.
    auto trace=std::make_shared<Trace>();trace->startup_state="Door:0";
    auto path=fs::current_path()/".controller-testdata"/"legacy-startup-door";
    {Controller c(path,true,[trace](const auto&){return std::make_unique<Fake>(trace);});rejects([&]{c.call("connect",{{"port","SIMULATOR"}});});expect(!trace->contains("\x18"),"Legacy startup behavior changed");}
    std::error_code ec;fs::remove_all(path,ec);
}
int main() {
    try {
        realtime_speed(); upload_validation(); checksum_and_bounds(); pause_stop_and_lock(); alarm_and_configuration(); physical_idle_and_manual(); immediate_controls_after_completion(); work_coordinate_offsets(); stop_returns_xyz();device_control_profiles();paper_contact_startup();stationary_startup_reset();
        std::cout<<"PASS: upload/hash/bounds, replay/restart, pause/stop/locks, alarm/config/final position, physical Idle/manual/relative origin\n";
    } catch (const std::exception& e) { std::cerr<<"FAIL: "<<e.what()<<'\n'; return 1; }
}
