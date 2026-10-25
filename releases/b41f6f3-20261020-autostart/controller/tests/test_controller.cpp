#include "controller.hpp"
#include "picosha2.h"
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
        if (trace->alarm && raw=="G1 Z10 F3000\n") { pending_alarm=true; return; }
        if (raw=="G1 X0 Y0 F3000\n") final=true;
        delegate->write(raw);
    }
    std::string read_line() override {
        if (pending_alarm) { pending_alarm=false; return "ALARM:1"; }
        auto line=delegate->read_line();
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
    expect(f.trace->contains("!\x18"),"Stop did not send reset");
    rejects([&]{f.c->call("resume",Json::object());}); f.start(); expect(f.c->state()["phase"]=="stopped","Stopped task replayed");
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
}
int main() {
    try {
        upload_validation(); checksum_and_bounds(); pause_stop_and_lock(); alarm_and_configuration(); physical_idle_and_manual();
        std::cout<<"PASS: upload/hash/bounds, replay/restart, pause/stop/locks, alarm/config/final position, physical Idle/manual/relative origin\n";
    } catch (const std::exception& e) { std::cerr<<"FAIL: "<<e.what()<<'\n'; return 1; }
}
